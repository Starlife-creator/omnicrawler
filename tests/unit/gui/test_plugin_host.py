"""Tests for GUI plugin host and plugin theme registration (Qt offscreen)."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel, QMainWindow

from omnicrawler.gui import design_system
from omnicrawler.gui.plugin_host import clear_plugin_ui, install_plugin_ui
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
    registry.register_background("demo.background", "演示背景")
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
        assert window.findChild(QDockWidget, "pluginBackgroundPanel_demo.background") is not None
    finally:
        window.close()


def test_theme_value_validation_rejects_injection() -> None:
    with pytest.raises(ValueError, match="非法颜色值"):
        design_system.register_plugin_theme("evil", "注入", {"primary": "url(javascript:alert(1))"})
    with pytest.raises(ValueError, match="未知主题令牌字段"):
        design_system.register_plugin_theme("evil2", "注入", {"not_a_field": "#112233"})


def test_background_host_scans_only_bounded_local_media(tmp_path) -> None:
    from omnicrawler.gui.background_host import discover_local_media

    (tmp_path / "ambient.png").write_bytes(b"image")
    (tmp_path / "motion.mp4").write_bytes(b"video")
    (tmp_path / "page.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    (tmp_path / "program.exe").write_bytes(b"not allowed")
    items = discover_local_media(tmp_path)
    assert [item.path.name for item in items] == ["ambient.png", "motion.mp4"]
    assert [item.kind for item in items] == ["image", "video"]


def test_background_surface_stays_below_controls_and_applies_safe_theme(
    app: QApplication, tmp_path
) -> None:
    from PySide6 import QtCore, QtGui
    from PySide6.QtWidgets import QPushButton, QWidget

    from omnicrawler.gui.background_host import BackgroundController

    image = tmp_path / "background.png"
    assert QtGui.QImage(32, 32, QtGui.QImage.Format.Format_RGB32).save(str(image))
    window = QMainWindow()
    window._settings = SimpleNamespace(high_contrast=False)
    central = QWidget(window)
    button = QPushButton("Run", central)
    button.setGeometry(20, 20, 100, 40)
    window.setCentralWidget(central)
    window.resize(400, 240)
    controller = BackgroundController(
        window,
        SimpleNamespace(
            background_id="test.safe", label="Safe", default_opacity=1.0,
            default_dim=0.15,
        ),
    )
    try:
        window.show()
        controller.set_scope("application")
        controller.set_media(image)
        app.processEvents()

        point = button.mapTo(window, button.rect().center())
        assert window.childAt(point) is not controller.layer
        assert controller.layer.testAttribute(
            QtCore.Qt.WidgetAttribute.WA_TransparentForMouseEvents
        )
        assert window.property("ambientBackground") is True
        controller.set_panel_opacity(76)
        assert "0.76" in window.styleSheet()
    finally:
        controller.close()
        window.close()
        QApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        app.processEvents()


def test_media_surface_v2_capabilities_are_bounded(app: QApplication) -> None:
    from omnicrawler.gui.media_surface import MediaSurfaceService

    window = QMainWindow()
    window._settings = SimpleNamespace(high_contrast=False)
    service = MediaSurfaceService(window, "demo", "Demo")
    try:
        capabilities = service.capabilities()
        assert capabilities["version"] == 2
        assert capabilities["input_passthrough"] is True
        assert capabilities["scopes"] == ["application", "workspace", "canvas"]
        assert capabilities["panel_opacity"]["minimum"] == 65
        configured = service.configure({
            "scope": "canvas", "panel_opacity": 70, "blur": 20,
            "opacity": 100,
        })
        assert configured["scope"] == "canvas"
        assert configured["panel_opacity"] == 70
        assert configured["blur"] == 20
    finally:
        service.close()
        window.close()
        from PySide6 import QtCore

        QApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        app.processEvents()


def test_market_blocks_unavailable_required_capability() -> None:
    from omnicrawler.gui.views.plugin_market import _install_block_reason

    reason = _install_block_reason(
        {
            "id": "future-plugin",
            "compatible_core": ">=0.1",
            "required_capabilities": {"future.magic": ">=1"},
        }
    )
    assert "不支持" in reason


def test_contract2_declarative_view_mounts_host_owned_widgets(app: QApplication) -> None:
    from PySide6.QtWidgets import QDockWidget, QPushButton

    class Adapter:
        surface = None

        def describe(self):
            return {
                "view_id": "safe.main", "title": "Safe View", "preferred_zone": "right",
                "movable": True, "resizable": True, "floatable": True,
                "default_width": 320, "default_height": 360,
                "minimum_width": 240, "minimum_height": 160,
                "components": [{
                    "type": "button", "id": "refresh", "label": "Refresh", "action": "refresh",
                }],
            }

        def bind_surface(self, surface):
            self.surface = surface

        def action(self, action, payload):
            return {"message": action}

    registry = Registry()
    adapter = Adapter()
    registry.declarative_views["safe-plugin"] = adapter
    window = QMainWindow()
    install_plugin_ui(window, registry)
    try:
        dock = window.findChild(QDockWidget, "declarativePluginView_safe-plugin_safe.main")
        assert dock is not None
        assert dock.findChild(QPushButton).text() == "Refresh"
        assert adapter.surface is not None
    finally:
        window.close()


def test_clear_plugin_ui_unmounts_declarative_view(app: QApplication) -> None:
    from PySide6 import QtCore
    from PySide6.QtWidgets import QDockWidget

    class Adapter:
        def describe(self):
            return {
                "view_id": "reload.main", "title": "Reload View", "preferred_zone": "right",
                "movable": True, "resizable": True, "floatable": True,
                "default_width": 320, "default_height": 360,
                "minimum_width": 240, "minimum_height": 160,
                "components": [],
            }

        def bind_surface(self, _surface):
            return None

    registry = Registry()
    registry.declarative_views["reload-plugin"] = Adapter()
    window = QMainWindow()
    install_plugin_ui(window, registry)
    assert window.findChild(
        QDockWidget, "declarativePluginView_reload-plugin_reload.main"
    ) is not None

    clear_plugin_ui(window)
    QApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)

    assert window.findChild(
        QDockWidget, "declarativePluginView_reload-plugin_reload.main"
    ) is None
    assert window._declarative_plugin_view_controllers == []
    window.close()
