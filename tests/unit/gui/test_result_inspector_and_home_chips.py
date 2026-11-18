"""ResultTable 字段完整度检查、一键 JSON/单元格复制与首页场景示例芯片单测。"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QModelIndex  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from omnicrawler.gui.home import HomePage, scenario_starter_specs  # noqa: E402
from omnicrawler.gui.views.result_table import (  # noqa: E402
    ResultTable,
    build_row_dict,
    compute_page_completeness,
    format_completeness_summary,
    format_row_inspector_text,
)

_APP: QApplication | None = None


@pytest.fixture(autouse=True)
def _ensure_qt_app() -> QApplication:
    global _APP
    _APP = QApplication.instance() or QApplication([])  # type: ignore[assignment]
    assert _APP is not None
    return _APP


def test_completeness_summary_distinguishes_full_partial_and_broken_columns() -> None:
    empty_stats = compute_page_completeness([], [])
    _msg, status = format_completeness_summary(empty_stats)
    assert status == "muted"

    full_stats = compute_page_completeness(
        ["title", "url"],
        [["Item A", "https://example.com/a"], ["Item B", "https://example.com/b"]],
    )
    full_text, full_status = format_completeness_summary(full_stats)
    assert full_status == "success"
    assert "100%" in full_text
    assert full_stats["incomplete_columns"] == []

    partial_stats = compute_page_completeness(
        ["title", "price", "author"],
        [
            ["Item A", "$10", ""],
            ["Item B", "", ""],
        ],
    )
    partial_text, partial_status = format_completeness_summary(partial_stats)
    assert partial_status == "danger", "存在整列全空（选择器失效）时必须标为 danger"
    assert "price (1/2)" in partial_text
    assert "author (0/2)" in partial_text

    warn_stats = compute_page_completeness(
        ["title", "price"],
        [["Item A", "$10"], ["Item B", ""]],
    )
    _warn_text, warn_status = format_completeness_summary(warn_stats)
    assert warn_status == "warning"


def test_row_inspector_formats_multiline_and_copies_valid_json(tmp_path: Path) -> None:
    row_dict = build_row_dict(["title", "summary", "tag"], ["Hello", "Line 1\nLine 2", ""])
    preview = format_row_inspector_text(row_dict, '{"selector": "h1"}')
    assert "title: Hello" in preview
    assert "Line 1\n    Line 2" in preview
    assert '"selector": "h1"' in preview

    csv_path = tmp_path / "sample.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["record_id", "title", "price"])
        writer.writerow(["r1", "Widget Pro", "99.0"])
        writer.writerow(["r2", "Widget Lite", ""])

    table = ResultTable()
    assert table._model.load_file(csv_path) is True
    stats = table.update_completeness_summary()
    assert stats["overall_pct"] == 83.3
    assert "price (1/2)" in table._completeness_label.text()

    target_idx = table._proxy.index(0, 1)
    table._table.setCurrentIndex(target_idx)
    table._show_evidence(target_idx, QModelIndex())

    assert table._copy_row_json_btn.isEnabled() is True
    assert table._copy_cell_btn.isEnabled() is True
    assert "Widget Pro" in table._evidence.toPlainText()

    copied_cell = table.copy_selected_cell()
    assert copied_cell == "Widget Pro"

    copied_json = table.copy_selected_row_json()
    parsed = json.loads(copied_json)
    assert parsed == {"record_id": "r1", "title": "Widget Pro", "price": "99.0"}
    table.deleteLater()


def test_home_scenario_starter_chips_prefill_preview_and_compile_cleanly() -> None:
    specs = scenario_starter_specs()
    assert [item[0] for item in specs] == ["news_list", "product_catalog", "json_api"]

    home = HomePage()
    emitted: list[object] = []
    home.natural_task_ready.connect(emitted.append)

    for key, _label, expected_prompt in specs:
        assert key in home.scenario_chips
        home.scenario_chips[key].click()
        assert home.task_input.toPlainText() == expected_prompt
        assert home.feedback.text().strip() != ""
        # 点击示例芯片只生成可编辑预览，不得替用户直接提交跳转
        assert emitted == []

    # 用户确认后点击「创建任务」应直接产出自然语言任务，不弹「无法判断意图」对话框
    home.create_button.click()
    assert len(emitted) == 1
    home.shutdown()
    home.deleteLater()
