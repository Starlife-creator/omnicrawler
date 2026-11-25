"""`build_quality_report` 接受已解析结果时，不得再 SELECT / 再 json.loads。

为什么要有这个文件（2026-11-25）：

导出链路原本同一份 `evidence_json` 解析两遍：

    exporters._export_delivery  -> SELECT + json.loads(evidence)
      └ build_quality_report    -> 再次 SELECT + 再次 json.loads(evidence)

2 万条实测多付：读库 59ms + 解析 46ms，且 `pii_summary` 那里还第三次解析了
`data_json`。现在两者都可由调用方传入。

本文件钉住「传了就不读库」，并证明**语义不变**（报告字段与自读版本一致）。
"""

from __future__ import annotations

import json
from pathlib import Path

from omnicrawler.core.config import AppConfig
from omnicrawler.quality import quality_report as qr
from omnicrawler.state.state_store import StateStore


def _seed(tmp_path: Path, count: int) -> tuple[AppConfig, StateStore, str]:
    work = tmp_path / "work"
    work.mkdir()
    raw = {
        "project": {"name": "t", "workspace": str(work)},
        "source": {"kind": "static_html", "seeds": ["https://example.com/l"]},
    }
    config = AppConfig(tmp_path / "c.yaml", tmp_path, raw, work)
    state = StateStore(work / "state.sqlite3")
    run_id = state.start_run("t", "c.yaml")
    rows = []
    for i in range(count):
        data = json.dumps({"title": f"T{i}", "email": "someone@example.com"})
        evidence = json.dumps({"_quality": {"score": 0.9, "review_required": i % 2 == 1}})
        rows.append((f"r{i}", f"https://example.com/{i}", "html_item", "2026-01-01",
                     data, evidence, run_id, f"fp{i}"))
    with state.conn:
        state.conn.executemany(
            "INSERT INTO records(record_id,source_url,record_type,created_at,"
            "data_json,evidence_json,run_id,request_fingerprint) "
            "VALUES(?,?,?,?,?,?,?,?)",
            rows,
        )
    return config, state, run_id


def test_supplied_evidence_means_no_extra_select(tmp_path: Path, monkeypatch) -> None:
    """传了 `parsed_evidence` 时不得再对 records 表发 SELECT。"""
    config, state, run_id = _seed(tmp_path, 5)
    calls: list[str] = []
    real = state.rows

    def counting(sql, params=()):
        calls.append(sql)
        return real(sql, params)

    evidence = [json.loads(r["evidence_json"]) for r in real("SELECT evidence_json FROM records", ())]
    data = [json.loads(r["data_json"]) for r in real("SELECT data_json FROM records", ())]
    calls.clear()
    # ★ 必须真的装上 —— 上一版只定义了 counting 却没 setattr，
    # 于是 calls 恒为空、断言恒真（假守卫）。反向断言时正是这条没有变红，
    # 才暴露出它没在守任何东西。
    monkeypatch.setattr(state, "rows", counting)

    qr.build_quality_report(
        config, state, run_id, parsed_evidence=evidence, parsed_data=data, record_total=5
    )

    offending = [sql for sql in calls if "records" in sql]
    assert not offending, f"传了已解析结果却仍查 records 表：{offending}"


def test_report_matches_self_read_path(tmp_path: Path) -> None:
    """传入已解析结果与自读，报告内容必须一致（语义不变）。"""
    config, state, run_id = _seed(tmp_path, 6)
    baseline = qr.build_quality_report(config, state, run_id)

    rows = state.rows("SELECT data_json, evidence_json FROM records", ())
    evidence = [json.loads(r["evidence_json"]) for r in rows]
    data = [json.loads(r["data_json"]) for r in rows]
    fast = qr.build_quality_report(
        config, state, run_id, parsed_evidence=evidence, parsed_data=data,
        record_total=len(rows),
    )

    for key in ("records", "skipped_malformed", "average_score", "entities_resolved"):
        assert baseline.get(key) == fast.get(key), f"字段 {key} 不一致"


def test_malformed_evidence_still_counted(tmp_path: Path) -> None:
    """已解析结果里混入非法值，仍要计入 skipped_malformed（不能静默变 0）。"""
    config, state, run_id = _seed(tmp_path, 3)
    rows = state.rows("SELECT evidence_json FROM records", ())
    evidence = [json.loads(r["evidence_json"]) for r in rows]
    evidence.append("not-a-dict")

    report = qr.build_quality_report(
        config, state, run_id, parsed_evidence=evidence, record_total=len(evidence),
    )
    assert report["skipped_malformed"] == 1, report.get("skipped_malformed")
