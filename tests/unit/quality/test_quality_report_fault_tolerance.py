"""S2.5.30：quality_report 脏数据容错 + run_id SQL 参数化。"""

from __future__ import annotations

import json
from pathlib import Path

from omnicrawl.core.config import load_config
from omnicrawl.quality.quality_report import build_quality_report
from omnicrawl.state import StateStore


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
    report = build_quality_report(config, StateStore(config.workspace / "state.sqlite3"), run_id)
    assert report["records"] == 2
    assert report["skipped_malformed"] == 1
    assert report["average_quality_score"] == 0.9


def test_run_id_with_quotes_is_safe(tmp_path: Path) -> None:
    config = load_config(_config(tmp_path))
    config.workspace.mkdir(parents=True, exist_ok=True)
    with StateStore(config.workspace / "state.sqlite3") as state:
        state.start_run("qr", str(config.path))
    report = build_quality_report(
        config, StateStore(config.workspace / "state.sqlite3"),
        "x' OR 1=1 --",
    )
    assert report["records"] == 0
    assert report["run_id"] == "x' OR 1=1 --"
