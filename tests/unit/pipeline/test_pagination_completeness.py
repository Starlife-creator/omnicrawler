"""分页完整性判据：「访问 N 页却只交付 M 条」必须**可见**（W3.2 / §5.2 #6）。

## 背景（审计 §5.2 #6 的剩余部分）

流水线原先只在摘要里给 `records` / `responses` 两个孤立数字，**没有判据**：
一个"翻了 20 页只采到 1 条"的任务会以 `succeeded` 结束、摘要看起来也很正常
（`records: 1` 本身不像错误），用户很可能把它当成功交付。

W3.2 的要求是**可见**：两个数字**无条件**写进 `output/summary.json` 的 `delivery` 块，
比值异常时再加一条告警（页面选择器过窄 / 分页没生效 / 详情解析失败都是常见成因）。

判定规则：`页数 ≥ _PAGINATION_GAP_MIN_PAGES` 且 `记录数 × 2 < 页数`（每页不足 0.5 条）。
页数太少时比值噪声大，**不报**（避免"1 页 0 条"这种正常空栏误报）。本文件守住两侧。
"""

from __future__ import annotations

import json
from pathlib import Path

from omnicrawler.core.config import load_config
from omnicrawler.pipeline.exporters import _PAGINATION_GAP_MIN_PAGES, export_all
from omnicrawler.state import StateStore


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "task.yaml"
    path.write_text(
        f"project: {{name: exp, workspace: {str(tmp_path / 'work').replace(chr(92), '/')}}}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        "extract:\n  mode: html\n  fields:\n    title: {selector: title}\n"
        "outputs:\n  jsonl: true\n  csv: true\n  xlsx: false\n",
        encoding="utf-8",
    )
    return path


def _seed(tmp_path: Path, *, pages: int, records: int) -> tuple[Path, str]:
    """种 *pages* 条响应与 *records* 条记录 —— 用来构造"页数 ≫ 记录数"。"""
    config_path = _config(tmp_path)
    config = load_config(config_path)
    config.workspace.mkdir(parents=True, exist_ok=True)
    with StateStore(config.workspace / "state.sqlite3") as state:
        run_id = state.start_run("exp", str(config_path))
        with state.conn:
            for index in range(pages):
                state.conn.execute(
                    "INSERT INTO responses(run_id, request_fingerprint, url, final_url, status_code,"
                    " content_type, size_bytes, content_sha256, changed, fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (run_id, f"fp{index}", f"https://example.org/p{index}", f"https://example.org/p{index}",
                     200, "text/html", 100, f"sha{index}", 0, "now"),
                )
            for index in range(records):
                state.conn.execute(
                    "INSERT INTO records(record_id, run_id, request_fingerprint, source_url, record_type,"
                    " data_json, evidence_json, created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (f"r{index}", run_id, f"fp{index}", "https://example.org/", "item",
                     json.dumps({"title": f"T{index}"}), json.dumps({"raw": "x"}), "now"),
                )
    return config_path, run_id


def _summary(tmp_path: Path, *, pages: int, records: int) -> dict:
    config_path, run_id = _seed(tmp_path, pages=pages, records=records)
    config = load_config(config_path)
    with StateStore(config.workspace / "state.sqlite3") as state:
        return export_all(config, state, run_id)


# ── ① 数字必须**无条件**可见 ─────────────────────────────────────────────


def test_delivery_counts_are_always_exposed(tmp_path: Path) -> None:
    summary = _summary(tmp_path, pages=2, records=1)  # 页数低于门槛：不该告警
    delivery = summary["delivery"]
    assert delivery["pages_visited"] == 2
    assert delivery["records_delivered"] == 1
    assert delivery["records_per_page"] == 0.5
    assert delivery["pagination_gap_suspected"] is False, "少量页数不该误报"


def test_summary_file_carries_the_delivery_block(tmp_path: Path) -> None:
    """用户看的是 `summary.json` —— 判据必须真的落到产物里，而不只是函数返回值。"""
    summary = _summary(tmp_path, pages=10, records=1)
    workspace = Path(str(summary["files"]["summary"])).parent
    on_disk = json.loads((workspace / "summary.json").read_text(encoding="utf-8"))
    assert on_disk["delivery"]["pages_visited"] == 10
    assert on_disk["delivery"]["records_delivered"] == 1


# ── ② 「页数 ≫ 记录数」必须报出来 ────────────────────────────────────────


def test_pagination_gap_is_reported(tmp_path: Path) -> None:
    summary = _summary(tmp_path, pages=10, records=1)
    delivery = summary["delivery"]
    assert delivery["pagination_gap_suspected"] is True
    warnings = " ".join(str(item) for item in (summary.get("warnings") or []))
    assert "分页完整性可疑" in warnings
    assert "10 页" in warnings and "1 条" in warnings, warnings


def test_no_false_alarm_when_records_are_dense(tmp_path: Path) -> None:
    summary = _summary(tmp_path, pages=10, records=10)
    assert summary["delivery"]["pagination_gap_suspected"] is False
    warnings = " ".join(str(item) for item in (summary.get("warnings") or []))
    assert "分页完整性可疑" not in warnings


def test_threshold_boundary_is_documented_and_enforced(tmp_path: Path) -> None:
    """边界：正好半页（记录数 × 2 == 页数）**不报**，低于半页才报。"""
    at_boundary = _summary(tmp_path, pages=_PAGINATION_GAP_MIN_PAGES * 2, records=_PAGINATION_GAP_MIN_PAGES)
    assert at_boundary["delivery"]["pagination_gap_suspected"] is False

    edge_dir = tmp_path / "edge"
    edge_dir.mkdir()
    config_path, run_id = _seed(edge_dir, pages=_PAGINATION_GAP_MIN_PAGES, records=1)
    config = load_config(config_path)
    with StateStore(config.workspace / "state.sqlite3") as state:
        below = export_all(config, state, run_id)
    assert below["delivery"]["pagination_gap_suspected"] is True
