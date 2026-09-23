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
            action_label=_("返回任务工作台"),
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
    """悬浮帮助按钮。

    ★ 此用例曾**静默失效**：它调用的是 `HelpTooltip(help_id=…, label=…)`，而
    `label` 参数早已不在控件 API 里（`TypeError`）。因为基线目录不随源码包分发、
    整个模块在没有基线时**整块 skip**，这个脱节长期没人发现 —— 生成本地基线后
    第一次运行就当场报错。help_id 必须是帮助注册表里**真实存在**的键
    （`get_help` 对未知 id 直接 `KeyError`）。
    """
    from omnicrawler.gui.widgets.help_tooltip import HelpTooltip

    for theme in THEMES:
        qapp.setProperty("omnicrawlerTheme", theme)
        widget = HelpTooltip("task.name")
        _snap(widget, "help_tooltip", theme)


def test_home_hero_snapshot(theme_manager, qapp):
    """首页 hero 区 —— V2"加大字号/留白层级"的落点。

    ★ 必须先关动效：`AmbientHero` 每 50ms 推进一次相位，装饰光斑位置随相位变化，
    截图不定影 ⇒ 快照会变成随机通过/失败（比没有快照更糟）。
    """
    from omnicrawler.gui.home import AmbientHero

    qapp.setProperty("omnicrawlerReducedMotion", True)
    try:
        for theme in THEMES:
            theme_manager._app.setProperty("omnicrawlerTheme", theme)
            widget = AmbientHero()
            _snap(widget, "home_hero", theme)
            widget._timer.stop()  # type: ignore[attr-defined]
    finally:
        qapp.setProperty("omnicrawlerReducedMotion", False)


def test_navigation_bar_snapshot(theme_manager):
    from PySide6.QtWidgets import QListWidget

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
