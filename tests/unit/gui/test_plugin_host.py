"""Tests for GUI plugin host and plugin theme registration (Qt offscreen)."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel, QMainWindow

from omnicrawler.gui import design_system
from omnicrawler.gui.plugin_host import install_plugin_ui
from omnicrawler.plugins.plugins import Registry


@pytest.fixture(scope="module")
def app() -> QApplication:
    application = QApplication.instance() or QApplication([])
    return application


def _registry_with_ui() -> Registry:
    registry = Registry()
    registry.register_theme("demo_theme", "演示主题", tokens={"primary": "#112233"})
    registry.register_ui_action("demo.action", "演示动作", lambda mw: None)
    registry.register_ui_panel("demo.panel", "演示面板", lambda mw: QLabel("面板", mw))
    registry.register_status_widget(lambda: QLabel("状态"))
    registry.register_status_widget(lambda: "not-a-widget")  # 失败项
    return registry


def test_install_plugin_ui_theme_registered(app: QApplication) -> None:
    registry = _registry_with_ui()
    window = QMainWindow()
    errors = install_plugin_ui(window, registry)
    window.close()

    tokens = design_system.plugin_theme_tokens("demo_theme")
    assert tokens is not None
    assert tokens.primary == "#112233"
    assert tokens.canvas  # base 令牌继承
    labels = dict(design_system.plugin_theme_labels())
    assert labels["演示主题"] == "demo_theme"
    assert any("状态小部件" in error for error in errors)


def test_install_plugin_ui_actions_panels(app: QApplication) -> None:
    from PySide6.QtWidgets import QDockWidget

    registry = _registry_with_ui()
    window = QMainWindow()
    install_plugin_ui(window, registry)
    try:
        plugin_menu = next(
            action.menu()
            for action in window.menuBar().actions()
            if action.menu() is not None and "插件" in action.menu().title()
        )
        action_labels = [action.text() for action in plugin_menu.actions()]
        assert "演示动作" in action_labels
        docks = window.findChildren(QDockWidget)
        assert any(dock.objectName() == "pluginPanel_demo.panel" for dock in docks)
        status_widgets = window.statusBar().findChildren(QLabel)
        assert any(widget.text() == "状态" for widget in status_widgets)
    finally:
        window.close()


def test_theme_value_validation_rejects_injection() -> None:
    with pytest.raises(ValueError, match="非法颜色值"):
        design_system.register_plugin_theme("evil", "注入", {"primary": "url(javascript:alert(1))"})
    with pytest.raises(ValueError, match="未知主题令牌字段"):
        design_system.register_plugin_theme("evil2", "注入", {"not_a_field": "#112233"})
