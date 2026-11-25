"""xlsx 导出必须用 **write_only 模式**（内存有界 + 8x 快）。

为什么要有这个文件（2026-11-25 实测）：

默认 `Workbook` 每写一个单元格都构造 `Cell` 对象并跑 `check_string`/`_bind_value`，
**所有单元格常驻内存**直到 `save()`。20k 行 × 8 列 = 16 万次构造，实测：

    普通 Workbook :  717 ms
    write_only    :   88 ms   -> 8.19x

比速度更重要的是**内存**：10 万行的导出在默认模式下会同时持有 80 万个 Cell 对象。

这是一条**契约**：导出的 xlsx 必须是流式写出，不得把整表物化在内存里。
测试通过与「内存无关」的方式断言 —— 查 `Workbook` 的构造参数，
因为「用 write_only」正是这条内存契约的实现形式。

同时钉住 write_only 下容易改坏的三件事（都已实测读回验证）：

1. write_only 下**没有 `workbook.active`**，必须 `create_sheet`；
2. 表头样式无法 `sheet[1]` 回读，必须先造 `WriteOnlyCell`；
3. `freeze_panes` / 多 sheet / 样式**都必须保留**。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicrawler.core.config import AppConfig
from omnicrawler.pipeline.exporters import _export_delivery
from omnicrawler.state.state_store import StateStore

openpyxl = pytest.importorskip("openpyxl")


def _workspace(tmp_path: Path) -> tuple[AppConfig, StateStore, str]:
    work = tmp_path / "work"
    work.mkdir()
    raw = {
        "project": {"name": "t", "workspace": str(work)},
        "source": {"kind": "static_html", "seeds": ["https://example.com/list"]},
        "outputs": {"formats": ["csv"], "xlsx": True},
    }
    config = AppConfig(tmp_path / "c.yaml", tmp_path, raw, work)
    state = StateStore(work / "state.sqlite3")
    run_id = state.start_run("t", "c.yaml")
    data = json.dumps({"title": "T", "url": "https://example.com/a"})
    evidence = json.dumps({"_quality": {"score": 0.9}})
    rows = [
        (f"r{i}", f"https://example.com/{i}", "html_item", "2026-01-01",
         data, evidence, run_id, f"fp{i}")
        for i in range(3)
    ]
    with state.conn:
        state.conn.executemany(
            "INSERT INTO records(record_id,source_url,record_type,created_at,"
            "data_json,evidence_json,run_id,request_fingerprint) "
            "VALUES(?,?,?,?,?,?,?,?)",
            rows,
        )
    return config, state, run_id


def test_export_constructs_workbook_in_write_only_mode(tmp_path: Path, monkeypatch) -> None:
    """契约：导出必须走 write_only —— 否则 10 万行会同时持有 80 万个 Cell 对象。"""
    calls: list[dict] = []
    real = openpyxl.Workbook

    def spy(*args, **kwargs):
        calls.append(dict(kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(openpyxl, "Workbook", spy)
    config, state, run_id = _workspace(tmp_path)
    _export_delivery(config, state, run_id)

    assert calls, "导出没有构造 Workbook"
    assert any(call.get("write_only") is True for call in calls), (
        f"xlsx 导出用了默认（非流式）Workbook，大表会把整表物化在内存里：{calls}"
    )


def test_write_only_still_produces_all_sheets_and_styles(tmp_path: Path) -> None:
    """write_only 下必须仍保留：4 个 sheet / 冻结首行 / 表头样式。"""
    config, state, run_id = _workspace(tmp_path)
    summary = _export_delivery(config, state, run_id)
    files = summary["files"]
    assert "xlsx" in files, f"没有产出 xlsx：{sorted(files)}"

    book = openpyxl.load_workbook(files["xlsx"])
    assert book.sheetnames == ["结构化记录", "质量与复核", "抓取清单", "错误记录"]

    sheet = book["结构化记录"]
    assert sheet.freeze_panes == "A2", "write_only 下冻结首行丢了"
    header = sheet["A1"]
    assert header.font.bold is True, "write_only 下需用 WriteOnlyCell 才能保留表头加粗"
    assert str(header.fill.fgColor.rgb).endswith("1F4E78"), "表头填充色丢了"


def test_written_rows_match_the_source_records(tmp_path: Path) -> None:
    """流式写出不得改变内容 —— 行数与首列值都要对得上。"""
    config, state, run_id = _workspace(tmp_path)
    summary = _export_delivery(config, state, run_id)
    book = openpyxl.load_workbook(summary["files"]["xlsx"])
    sheet = book["结构化记录"]
    values = [[cell.value for cell in row] for row in sheet.iter_rows(min_row=2)]
    assert len(values) == 3, f"应有 3 条记录，实际 {len(values)}"
    assert {row[0] for row in values} == {"r0", "r1", "r2"}
