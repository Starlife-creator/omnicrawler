"""Snapshot regression tests for key GUI components.

Each test renders a widget under three themes, captures a screenshot,
and compares against stored baselines.

Run with OMNI_BASELINE=1 to generate new baselines.
"""

from __future__ import annotations

import os

import pytest

from . import BASELINE_DIR
from .conftest import compare_snapshot

if not BASELINE_DIR.is_dir() and not os.environ.get("OMNI_BASELINE"):
    pytest.skip(
        "缺少基线截图目录 tests/gui/visual/baselines/（源码包未附带）；"
        "先运行 OMNI_BASELINE=1 pytest tests/gui/visual/ 生成基线后再启用",
        allow_module_level=True,
    )

THEMES = ["light", "dark", "high_contrast"]


def _snap(widget, name: str, theme: str) -> None:
    """Helper: show widget, grab screenshot, compare."""
    widget.show()
    widget.resize(400, 200)
    widget.repaint()
    pixmap = widget.grab()
    result = compare_snapshot(name, theme, pixmap)
    widget.hide()
    if not result.get("match"):
        raise AssertionError(f"Visual regression for {name}@{theme}: {result}")


def test_empty_state_snapshot(theme_manager):
    from omnicrawler.gui.i18n import _
    from omnicrawler.gui.widgets.empty_state import EmptyState

    for theme in THEMES:
        theme_manager._app.setProperty("omnicrawlerTheme", theme)
        widget = EmptyState(
            icon="✓",
            title=_("专业复核台 · 功能开发中"),
            description=_("此处将提供按风险优先排序的字段复核界面"),
            action_label=_("返回配置向导"),
        )
        _snap(widget, "empty_state", theme)


def test_status_indicator_snapshot(theme_manager):
    from omnicrawler.gui.widgets.status_indicator import StatusIndicator

    for theme in THEMES:
        theme_manager._app.setProperty("omnicrawlerTheme", theme)
        widget = StatusIndicator()
        for state in ("idle", "running", "finished", "error"):
            widget.state = state
            _snap(widget, f"status_indicator_{state}", theme)


def test_help_tooltip_snapshot(theme_manager, qapp):
    from omnicrawler.gui.i18n import _
    from omnicrawler.gui.widgets.help_tooltip import HelpTooltip

    for theme in THEMES:
        qapp.setProperty("omnicrawlerTheme", theme)
        widget = HelpTooltip(help_id="test", label=_("帮助"))
        _snap(widget, "help_tooltip", theme)


def test_navigation_bar_snapshot(theme_manager):
    from PyQt6.QtWidgets import QListWidget

    from omnicrawler.gui.design_system import ThemeManager

    for theme in THEMES:
        ThemeManager.instance().apply(theme_manager._app, theme)
        nav = QListWidget()
        nav.setObjectName("mainNavigation")
        nav.addItem("首页")
        nav.addItem("结果与复核")
        nav.addItem("设置")
        nav.setCurrentRow(0)
        _snap(nav, "navigation_bar", theme)


def test_toast_snapshot(theme_manager, qapp):
    from omnicrawler.gui.design_system import ThemeManager
    from omnicrawler.gui.widgets.toast import Toast

    for theme in THEMES:
        ThemeManager.instance().apply(qapp, theme)
        for kind in ("success", "warning", "error", "info"):
            toast = Toast(f"Test {kind} message", kind=kind, duration=9999)
            _snap(toast, f"toast_{kind}", theme)
