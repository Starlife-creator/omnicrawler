"""S2.5.30：quality_report 脏数据容错 + run_id SQL 参数化。"""

from __future__ import annotations

import json
from pathlib import Path

from omnicrawler.core.config import load_config
from omnicrawler.quality.quality_report import build_quality_report
from omnicrawler.state import StateStore


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "task.yaml"
    path.write_text(
        f"project: {{name: qr, workspace: '{tmp_path / 'work'}'}}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    return path


def test_quality_report_survives_malformed_cells(tmp_path: Path) -> None:
    config = load_config(_config(tmp_path))
    config.workspace.mkdir(parents=True, exist_ok=True)
    with StateStore(config.workspace / "state.sqlite3") as state:
        run_id = state.start_run("qr", str(config.path))
        with state.conn:
            state.conn.execute(
                "INSERT INTO records(record_id, run_id, request_fingerprint, source_url, record_type, data_json, evidence_json, created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                ("r1", run_id, "fp1", "https://example.org/1", "item",
                 json.dumps({"title": "ok"}), json.dumps({"_quality": {"score": 0.9}}), "now"),
            )
            state.conn.execute(
                "INSERT INTO records(record_id, run_id, request_fingerprint, source_url, record_type, data_json, evidence_json, created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                ("r2", run_id, "fp2", "https://example.org/2", "item",
                 "{broken data json", "{broken evidence json", "now"),
            )
            state.conn.execute(
                "INSERT INTO records(record_id, run_id, request_fingerprint, source_url, record_type, data_json, evidence_json, created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                ("r3", run_id, "fp3", "https://example.org/3", "item",
                 json.dumps({"title": "ok2"}), json.dumps({"_quality": {"score": 0.5}}), "now"),
            )
    report = build_quality_report(config, StateStore(config.workspace / "state.sqlite3"), run_id)
    # 畸形记录计入 total 但排除在平均之外：avg=(0.9+0.5)/2=0.7。
    # 若畸形记录被错误按 0 计入平均，avg 将变为 0.4667 ≠ 0.7（判别式断言）。
    assert report["records"] == 3
    assert report["skipped_malformed"] == 1
    assert report["average_quality_score"] == 0.7


def test_run_id_with_quotes_is_rejected(tmp_path: Path) -> None:
    """B04-003：非法 run_id（SQL 注入形态）被集中形态校验拒绝（fail-closed）。

    参数化仍保证注入不执行；集中校验更进一步在入口拒绝非法形态。
    """
    import pytest

    config = load_config(_config(tmp_path))
    config.workspace.mkdir(parents=True, exist_ok=True)
    with StateStore(config.workspace / "state.sqlite3") as state:
        state.start_run("qr", str(config.path))
    with pytest.raises(ValueError, match="run_id 含非法字符"):
        build_quality_report(
            config, StateStore(config.workspace / "state.sqlite3"),
            "x' OR 1=1 --",
        )
