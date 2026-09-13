"""INV-004 的证据：重处理清派生记录，但**不覆盖原始证据**。

`docs/SECURITY_AND_COMPLIANCE.md` 的 INV-004 声明「重处理不覆盖原始证据」，实现列为
「raw archive 按响应哈希归档、派生阶段重置」。其证据列原先指向 `test_v110_features.py`，
而该文件**全仓不存在**（2026-09-13 实测），故本文件补上真实证据。

被验证的契约来自 `state.state_store_records.reset_record_stage` 的 docstring：
"Clear derived record outputs **while preserving responses and raw archives**"：

1. **派生输出被清除**：`records` / `quality_stats` / `semantic_changes` / `record_versions`；
2. **原始证据保留**：`responses` 行与其 `raw_path` 不动，且磁盘上的原始归档内容未被改写。

配套证据：`tests/unit/services/test_replay.py`（按归档重处理时的完整性校验：
归档缺失判 `archive_missing`、DOM 哈希不符判 `dom_changed`）、
`tests/unit/pipeline/test_pipeline.py`（`incremental.archive_raw` 会真实落 `raw_path`）。
"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.core.models import CrawlRequest, ExtractedRecord, FetchResult
from omnicrawler.state import StateStore

_URL = "https://example.org/page"
_HTML = b"<html><body><h1>title</h1></body></html>"

_DERIVED_TABLES = ("records", "quality_stats", "semantic_changes", "record_versions")


def _count(state: StateStore, table: str, run_id: str) -> int:
    return int(state.rows(f"SELECT COUNT(*) AS n FROM {table} WHERE run_id=?", (run_id,))[0]["n"])


def test_reset_record_stage_clears_derived_but_keeps_raw_evidence(tmp_path: Path) -> None:
    """重处理前重置派生阶段：派生表清空，`responses` 与磁盘归档原样保留。"""
    raw = tmp_path / "raw" / "page.html"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(_HTML)

    with StateStore(tmp_path / "state.sqlite3") as state:
        run_id = state.start_run("inv004", "config.yaml")
        request = CrawlRequest(_URL)
        state.save_response(
            run_id,
            FetchResult(request, _URL, 200, {"content-type": "text/html"}, _HTML, 0.1),
            str(raw),
        )
        record = ExtractedRecord(_URL, "item", {"id": 1, "title": "t"})
        state.save_records(run_id, request, [record])
        state.track_semantic_changes(run_id, [record])

        # 前置：派生输出与原始证据都在
        assert _count(state, "records", run_id) == 1
        assert _count(state, "semantic_changes", run_id) == 1
        assert _count(state, "responses", run_id) == 1
        raw_path_before = state.rows(
            "SELECT raw_path FROM responses WHERE run_id=?", (run_id,)
        )[0]["raw_path"]

        reset = state.reset_record_stage(run_id)

        # ① 派生输出被清除
        assert reset["records"] == 1, f"应报告清掉的记录数：{reset}"
        for table in _DERIVED_TABLES:
            assert _count(state, table, run_id) == 0, f"{table} 应被清空（派生输出）"

        # ② 原始证据保留：responses 行、raw_path 与磁盘内容都不动
        assert _count(state, "responses", run_id) == 1, "responses（原始证据）绝不能被清掉"
        raw_path_after = state.rows(
            "SELECT raw_path FROM responses WHERE run_id=?", (run_id,)
        )[0]["raw_path"]
        assert raw_path_after == raw_path_before, "raw_path 不应被改写"
        assert Path(str(raw_path_after)).is_file(), "原始归档文件必须仍在磁盘上"
        assert raw.read_bytes() == _HTML, "原始归档内容不得被覆盖"
