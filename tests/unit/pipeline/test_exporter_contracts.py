"""S3.4.1：导出器修正（列顺序/csv 开关/类型推断/jsonl 去重/xlsx 上限）。"""

from __future__ import annotations

import json
from pathlib import Path

from omnicrawl.core.config import load_config
from omnicrawl.pipeline.exporters import _infer_column_type, export_all
from omnicrawl.state import StateStore


def _config(tmp_path: Path, **outputs: object) -> Path:
    path = tmp_path / "task.yaml"
    flags = " ".join(f"{key}: {value}" for key, value in outputs.items())
    path.write_text(
        "project: {name: exp, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        f"extract:\n  mode: html\n  fields:\n    title: {{selector: title}}\n    amount: {{selector: .amount}}\n"
        f"outputs:\n  jsonl: true\n  csv: true\n  xlsx: false\n  {flags}\n",
        encoding="utf-8",
    )
    return path


def _seed_records(tmp_path: Path) -> tuple[Path, str]:
    config_path = _config(tmp_path)
    config = load_config(config_path)
    config.workspace.mkdir(parents=True, exist_ok=True)
    with StateStore(config.workspace / "state.sqlite3") as state:
        run_id = state.start_run("exp", str(config_path))
        with state.conn:
            for index, title in enumerate(("Alpha", "Beta")):
                state.conn.execute(
                    "INSERT INTO records(record_id, run_id, request_fingerprint, source_url, record_type, data_json, evidence_json, created_at) "
                    "VALUES(?,?,?,?,?,?,?,?)",
                    (f"r{index}", run_id, f"fp{index}", "https://example.org/", "item",
                     json.dumps({"title": title, "amount": 1200 + index}),
                     json.dumps({"raw": title}), "now"),
                )
    return config_path, run_id


def test_csv_columns_follow_field_order(tmp_path: Path) -> None:
    config_path, run_id = _seed_records(tmp_path)
    config = load_config(config_path)
    with StateStore(config.workspace / "state.sqlite3") as state:
        export_all(config, state, run_id)
    header = (config.workspace / "output" / "records.csv").read_text(
        encoding="utf-8-sig"
    ).splitlines()[0]
    columns = header.split(",")
    assert columns.index("title") < columns.index("amount")  # extract.fields 定义顺序
    assert columns[0] == "record_id"


def test_responses_csv_respects_csv_switch(tmp_path: Path) -> None:
    config_path = _config(tmp_path, csv=False)
    config = load_config(config_path)
    config.workspace.mkdir(parents=True, exist_ok=True)
    with StateStore(config.workspace / "state.sqlite3") as state:
        run_id = state.start_run("exp", str(config_path))
        export_all(config, state, run_id)
    output = config.workspace / "output"
    assert not (output / "responses.csv").exists()
    assert not (output / "errors.csv").exists()
    assert (output / "records.csv").exists() is False  # 主 CSV 也关闭


def test_responses_csv_escapes_formula_injection(tmp_path: Path) -> None:
    """B06-001：responses.csv 的 content_type（远程可控）必须以 excel_safe 转义。"""
    config_path = _config(tmp_path, csv=True)
    config = load_config(config_path)
    config.workspace.mkdir(parents=True, exist_ok=True)
    with StateStore(config.workspace / "state.sqlite3") as state:
        run_id = state.start_run("exp", str(config_path))
        with state.conn:
            state.conn.execute(
                "INSERT INTO responses(run_id, request_fingerprint, url, final_url, status_code, content_type, size_bytes, content_sha256, raw_path, changed, elapsed_seconds, fetched_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, "fp-csv", "https://example.org/", "https://example.org/", 200, "=cmd|' /C calc'!A1",
                 5, "abc123", "/tmp/raw", 0, 0.1, "now"),
            )
        export_all(config, state, run_id)
    lines = (config.workspace / "output" / "responses.csv").read_text(
        encoding="utf-8-sig"
    ).splitlines()
    assert any("'=cmd|' /C calc'!A1" in line for line in lines)


def test_infer_column_type() -> None:
    records = [
        {"a": 1, "b": 1.5, "c": True, "d": "x"},
        {"a": 2, "b": 2.0, "c": False, "d": "y"},
    ]
    assert _infer_column_type("a", records) == "INTEGER"
    assert _infer_column_type("b", records) == "DOUBLE"
    assert _infer_column_type("c", records) == "BOOLEAN"
    assert _infer_column_type("d", records) == "VARCHAR"
    assert _infer_column_type("missing", records) == "VARCHAR"


def test_jsonl_does_not_duplicate_data_columns(tmp_path: Path) -> None:
    config_path, run_id = _seed_records(tmp_path)
    config = load_config(config_path)
    with StateStore(config.workspace / "state.sqlite3") as state:
        export_all(config, state, run_id)
    lines = (config.workspace / "output" / "records.jsonl").read_text(
        encoding="utf-8"
    ).splitlines()
    first = json.loads(lines[0])
    assert "data_json" not in first  # 原始列已剔除
    assert "evidence_json" not in first
    assert first["data"] == {"title": "Alpha", "amount": 1200}
    assert first["evidence"] == {"raw": "Alpha"}
