"""导出时同一行数据只能解析一次 JSON。

为什么要有这个文件（2026-11-25 实测）：

改动前 2 万条记录调用 `json.loads` **12 万次** —— 每行 6 次，而每行只有
2 个 JSON 列。同一份 `data_json` / `evidence_json` 被三处各自重复解析：

```
exporters.py:170   展开 records 用      data
exporters.py:183   构造 quality_rows 用 evidence
exporters.py:221   写 jsonl 用          data + evidence
```

这不是"会不会更准"的问题，是**同一份字节被解析三遍**。
演进（都发生在 2026-11-25）：
   6x/行  <- 三处各自 SELECT + 各自 json.loads
   4x/行  <- export 内部合并
   2x/行  <- quality_report 改为接受已解析结果（终态）

本文件用**计数**断言，而不是计时 —— 次数是确定性的，耗时会随机器波动。
断言用**精确等于** 2x：低于它说明有地方被跳过（可能丢数据），高于它说明又有人新增了独立的 SELECT + json.loads。
"""

from __future__ import annotations

import json
from pathlib import Path

from omnicrawler.core.config import AppConfig
from omnicrawler.pipeline import exporters
from omnicrawler.state.state_store import StateStore


def _seed(tmp_path: Path, count: int) -> tuple[AppConfig, StateStore, str]:
    work = tmp_path / "work"
    work.mkdir()
    raw = {
        "project": {"name": "t", "workspace": str(work)},
        "source": {"kind": "static_html", "seeds": ["https://example.com/list"]},
        "outputs": {"formats": ["csv", "jsonl"], "xlsx": False},
    }
    config = AppConfig(tmp_path / "c.yaml", tmp_path, raw, work)
    state = StateStore(work / "state.sqlite3")
    run_id = state.start_run("t", "c.yaml")
    data = json.dumps({"title": "T", "price": "9.9"})
    evidence = json.dumps({"_quality": {"score": 0.9}})
    rows = [
        (f"r{i}", f"https://example.com/{i}", "html_item", "2026-01-01",
         data, evidence, run_id, f"fp{i}")
        for i in range(count)
    ]
    with state.conn:
        state.conn.executemany(
            "INSERT INTO records(record_id,source_url,record_type,created_at,"
            "data_json,evidence_json,run_id,request_fingerprint) "
            "VALUES(?,?,?,?,?,?,?,?)",
            rows,
        )
    return config, state, run_id


def test_each_json_column_is_parsed_once_per_row(tmp_path: Path, monkeypatch) -> None:
    """每行的 data_json / evidence_json 各自只解析一次（2 次/行，不是 6 次）。"""
    count = 10
    config, state, run_id = _seed(tmp_path, count)

    calls = [0]
    real = json.loads

    def counting(*args, **kwargs):
        calls[0] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(exporters.json, "loads", counting)
    exporters._export_delivery(config, state, run_id)

    #: 每行恰好 2 次：`data_json` 与 `evidence_json` **各一次**。
    #:
    #: 演进（都发生在 2026-11-25）：
    #:   6x/行 <- 三处各自 SELECT + 各自 json.loads（export / quality_rows / jsonl）
    #:   4x/行 <- export 内部合并
    #:   2x/行 <- quality_report 改为接受已解析结果（终态）
    #:
    #: 用**精确等于**而不是上界：低于它说明有解析被跳过（可能丢数据），
    #: 高于它说明又有人新增了独立的 SELECT + json.loads。
    expected = count * 2
    assert calls[0] == expected, (
        f"{count} 行应恰好解析 {expected} 次（每行 data + evidence 各一次），"
        f"实际 {calls[0]} 次（{calls[0] / count:.1f}x/行）。"
        f" 多了 = 有人新增了独立的 SELECT + json.loads；少了 = 有解析被跳过"
    )


def test_dedup_does_not_change_output(tmp_path: Path) -> None:
    """减少解析不得改变产物内容：jsonl 里的 data/evidence 必须仍是解析后的对象。"""
    config, state, run_id = _seed(tmp_path, 3)
    summary = exporters._export_delivery(config, state, run_id)

    lines = Path(summary["files"]["jsonl"]).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert first["data"] == {"title": "T", "price": "9.9"}, first["data"]
    assert first["evidence"]["_quality"]["score"] == 0.9
    # 原始列不应残留
    assert "data_json" not in first
    assert "evidence_json" not in first
