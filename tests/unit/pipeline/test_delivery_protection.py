"""走查 R1.3：**复跑不得毁掉上一次的交付**（空交付必须先保护已有输出）。

背景（0.13.0 实测，2026-09-18 走查 R1.3）：同一工作区第二次 `run` 交付 0 条时，
`records.jsonl` 被写成 **0 字节**、`records.csv` 只剩表头。数据其实还在断点库里
（`omnicrawler export` 可恢复），但用户看到的是「上次的结果没了」——
这是对《优化方案》§4.2「已有有效输出得到保护」的直接违反。

本文件守住「覆盖前先另存 + 摘要里说明去向」这一最小形态；
完整形态（导出按 run 维度隔离 + latest 指针）见《优化方案》§6.5 R1.3，属独立批次。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from omnicrawler.core.config import load_config
from omnicrawler.pipeline.exporters import export_all
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


def _add_run(config, *, records: int) -> str:
    """在同一工作区里新起一次 run 并写入 *records* 条记录，返回 run_id。"""
    with StateStore(config.workspace / "state.sqlite3") as state:
        run_id = state.start_run("exp", "task.yaml")
        with state.conn:
            for index in range(records):
                state.conn.execute(
                    "INSERT INTO records(record_id, run_id, request_fingerprint, source_url, record_type,"
                    " data_json, evidence_json, created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (f"{run_id}-r{index}", run_id, f"fp{index}", "https://example.org/", "item",
                     json.dumps({"title": f"T{index}"}), json.dumps({"raw": "x"}), "now"),
                )
    return run_id


def _export(config, run_id: str) -> dict:
    with StateStore(config.workspace / "state.sqlite3") as state:
        return export_all(config, state, run_id)


def _rows(path: Path) -> int:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return max(0, sum(1 for _ in csv.reader(handle)) - 1)


def test_empty_rerun_preserves_previous_delivery(tmp_path: Path) -> None:
    """第一次 5 条 → 第二次 0 条：上一次的交付必须被另存，且摘要说明去向。"""
    config = load_config(_config(tmp_path))

    first = _add_run(config, records=5)
    summary_first = _export(config, first)
    output = Path(summary_first["files"]["summary"]).parent
    assert _rows(output / "records.csv") == 5

    second = _add_run(config, records=0)
    summary_second = _export(config, second)

    saved = sorted((output / "previous").glob("*_records.csv"))
    assert saved, "空交付覆盖前必须另存上一次的交付"
    assert _rows(saved[-1]) == 5, "另存的内容必须是完整的上一次交付"

    warnings = " ".join(str(item) for item in (summary_second.get("warnings") or []))
    assert "另存" in warnings, warnings
    assert "previous/" in warnings, "必须告诉用户去哪儿找"
    assert "export" in warnings, "必须给出恢复路径"


def test_jsonl_backup_is_also_kept(tmp_path: Path) -> None:
    """records.jsonl 曾被写成 0 字节 —— 它同样要被保护。"""
    config = load_config(_config(tmp_path))

    first = _add_run(config, records=3)
    summary_first = _export(config, first)
    output = Path(summary_first["files"]["summary"]).parent
    assert (output / "records.jsonl").read_text(encoding="utf-8").strip()

    second = _add_run(config, records=0)
    _export(config, second)

    backups = sorted((output / "previous").glob("*_records.jsonl"))
    assert backups, "records.jsonl 也必须被另存"
    assert len(backups[-1].read_text(encoding="utf-8").strip().splitlines()) == 3


def test_no_backup_when_there_is_nothing_to_protect(tmp_path: Path) -> None:
    """首次就是空交付 ⇒ 不该凭空造出 previous/，也不该告警（避免噪声）。"""
    config = load_config(_config(tmp_path))
    run_id = _add_run(config, records=0)
    summary = _export(config, run_id)
    output = Path(summary["files"]["summary"]).parent

    assert not (output / "previous").exists()
    warnings = " ".join(str(item) for item in (summary.get("warnings") or []))
    assert "另存" not in warnings


def test_previous_header_only_file_is_not_worth_backing_up(tmp_path: Path) -> None:
    """上一次只剩表头（本身就是空交付）⇒ 不算"有效输出"，不必另存。"""
    config = load_config(_config(tmp_path))
    first = _add_run(config, records=0)
    summary_first = _export(config, first)
    output = Path(summary_first["files"]["summary"]).parent

    second = _add_run(config, records=0)
    summary_second = _export(config, second)

    assert not (output / "previous").exists(), "仅表头不构成需要保护的有效输出"
    warnings = " ".join(str(item) for item in (summary_second.get("warnings") or []))
    assert "另存" not in warnings
