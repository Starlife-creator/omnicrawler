from __future__ import annotations

import importlib.util

import pytest

from omnicrawler.services.help_registry import (
    HELP_ENTRIES,
    NON_OBVIOUS_CONTROL_HELP_IDS,
    contextual_advice,
    get_help,
    search_help,
)


def test_help_registry_has_complete_eight_part_mode_aware_offline_content() -> None:
    assert NON_OBVIOUS_CONTROL_HELP_IDS == frozenset(HELP_ENTRIES)
    for help_id, entry in HELP_ENTRIES.items():
        assert entry.help_id == help_id
        assert all((entry.what, entry.why, entry.how, entry.example, entry.limitations,
                    entry.common_errors, entry.default_behavior, entry.change_impact))
        assert len(entry.full_text("simple")) > len(entry.short("simple"))
    assert get_help("source.seed").auto_action == "inspect_seed"
    assert [entry.help_id for entry in search_help("翻页 cursor")] == ["source.pagination"]
    assert "OCR组件" in contextual_advice("processors.pdf", {"process_pdf": True, "ocr_component": False})
    with pytest.raises(KeyError):
        get_help("missing.help")


@pytest.mark.skipif(importlib.util.find_spec("PyQt6") is None, reason="PyQt6 required")
def test_every_declared_non_obvious_control_is_bound_to_question_mark_and_help_center(monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    from omnicrawler.gui.main import MainWindow
    from omnicrawler.gui.widgets.help_tooltip import HelpTooltip

    monkeypatch.setattr(MainWindow, "_on_first_launch", lambda self: None)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    bound = {button.help_id for button in window.findChildren(HelpTooltip)}
    assert bound == NON_OBVIOUS_CONTROL_HELP_IDS
    window._help_center.show_help("source.pagination")
    assert window._help_center.isHidden() is False
    assert "如何填写" in window._help_center.details.toPlainText()
    window._help_center.search.setText("Excel")
    assert window._help_center.results.count() >= 1
    # 在关闭窗口前处理待处理事件，避免 processEvents 时访问已析构的 widget
    app.processEvents()
    window.close()
    app.processEvents()
