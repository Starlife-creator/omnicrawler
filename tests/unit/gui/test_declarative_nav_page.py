"""W6-A 端到端：`preferred_zone: main` 走导航页而不是侧栏面板。

单元层的插件页行为已由 `test_plugin_nav_page.py` 覆盖。这里验的是**接线**：
描述符里写 `main` 时，控制器真的不建 `QDockWidget`、真的进导航页、
并且 `close()` 真的把它摘干净。
"""

from __future__ import annotations

from typing import Any

import pytest
from PySide6 import QtCore, QtWidgets

from omnicrawler.gui import declarative_view_host as dvh_module
from omnicrawler.gui.declarative_view_host import DeclarativeViewController
from omnicrawler.gui.delegates.plugin_nav_page import (
    PLUGIN_NAV_GROUP_KEY,
    adopt_builtin_nav_pages,
)


@pytest.fixture(scope="module")
def qapp() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _StubSurface:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _StubAdapter:
    def __init__(self, descriptor: dict[str, Any]) -> None:
        self._descriptor = descriptor

    def describe(self) -> dict[str, Any]:
        return self._descriptor

    def bind_surface(self, surface: Any) -> None:
        del surface

    def bind_progress_relay(self, relay: Any) -> None:
        del relay


def _descriptor(zone: str) -> dict[str, Any]:
    return {
        "view_id": "community-guide",
        "title": "Community Guide",
        "preferred_zone": zone,
        "movable": True,
        "resizable": True,
        "floatable": True,
        "default_width": 380,
        "default_height": 640,
        "minimum_width": 240,
        "minimum_height": 160,
        "components": [{"type": "label", "id": "title", "text": "Hello"}],
    }


def _window(qapp: QtWidgets.QApplication) -> QtWidgets.QMainWindow:
    """最小可承载导航页的宿主窗口。

    必须有 `_nav` / `_stack` / 三张映射表 —— 与 `CentralAreaBuilder` 建出来的
    结构契约一致。用替身而不是真 `MainWindow`：真窗口装配要几秒，而这些判据
    只依赖结构契约（真窗口的一致性另有一条测试覆盖）。
    """
    window = QtWidgets.QMainWindow()
    central = QtWidgets.QWidget(window)
    layout = QtWidgets.QVBoxLayout(central)
    nav = QtWidgets.QListWidget(central)
    stack = QtWidgets.QStackedWidget(central)
    layout.addWidget(nav)
    layout.addWidget(stack)
    window.setCentralWidget(central)
    window._nav = nav
    window._stack = stack
    window._nav_pages = {}
    window._nav_row_by_key = {}
    window._nav_key_by_row = {}
    # 一条内置条目，用于确认插件页不会挤掉它。
    home = QtWidgets.QWidget()
    stack.addWidget(home)
    item = QtWidgets.QListWidgetItem("Home")
    item.setData(QtCore.Qt.ItemDataRole.UserRole, "home")
    nav.addItem(item)
    window._nav_pages = {0: 0}
    window._nav_row_by_key = {"home": 0}
    window._nav_key_by_row = {0: "home"}
    adopt_builtin_nav_pages(window)
    return window


@pytest.fixture()
def no_media(monkeypatch: pytest.MonkeyPatch) -> None:
    """替换媒体面：这条路径不需要 QtMultimedia 重型栈。"""
    monkeypatch.setattr(dvh_module, "MediaSurfaceService", _StubSurface)


def test_main_zone_does_not_create_a_dock(qapp, no_media) -> None:
    """★ 核心判据：`main` 模式**不建 QDockWidget**。

    建了就说明还走侧栏面板那条路 —— 190px 宽放不下整页内容。
    """
    window = _window(qapp)
    controller = DeclarativeViewController(window, "guide", _StubAdapter(_descriptor("main")))
    try:
        assert controller.is_nav_page is True
        assert controller.dock is None
        assert controller.page is not None
    finally:
        controller.close()
        window.close()


def test_right_zone_still_creates_a_dock(qapp, no_media) -> None:
    """★ 反方向：`right` 仍走侧栏面板，行为不变。"""
    window = _window(qapp)
    controller = DeclarativeViewController(window, "panel", _StubAdapter(_descriptor("right")))
    try:
        assert controller.is_nav_page is False
        assert controller.dock is not None
        assert controller.page is None
    finally:
        controller.close()
        window.close()


def test_main_zone_registers_a_nav_row(qapp, no_media) -> None:
    """导航行必须出现，并且带上「插件页面」分组标题。"""
    window = _window(qapp)
    controller = DeclarativeViewController(window, "guide", _StubAdapter(_descriptor("main")))
    try:
        keys = set(window._nav_row_by_key)
        assert any(key.startswith("plugin:") for key in keys)
        assert PLUGIN_NAV_GROUP_KEY in keys
        assert "home" in keys, "内置条目被挤掉了"
    finally:
        controller.close()
        window.close()


def test_close_removes_the_nav_page_and_group(qapp, no_media) -> None:
    """★ 卸载后不许留下任何「点了就空」的入口。"""
    window = _window(qapp)
    controller = DeclarativeViewController(window, "guide", _StubAdapter(_descriptor("main")))
    controller.close()
    keys = set(window._nav_row_by_key)
    assert keys == {"home"}, f"卸载后残留: {keys}"
    assert PLUGIN_NAV_GROUP_KEY not in keys
    assert window._nav.count() == 1
    window.close()


def test_rebuild_keeps_the_page_alive(qapp, no_media) -> None:
    """★ `view.action` 触发整页重建后，导航行与页面对象仍有效。

    重建走的是「换布局」而不是换页面 —— 换页面会让导航行指向已销毁的对象。
    """
    window = _window(qapp)
    controller = DeclarativeViewController(window, "guide", _StubAdapter(_descriptor("main")))
    try:
        page = controller.page
        updated = _descriptor("main")
        updated["components"] = [{"type": "label", "id": "title", "text": "Updated"}]
        controller._rebuild(updated)
        assert controller.page is page, "重建换掉了页面对象，导航行会指向已销毁对象"
        assert page is not None and page.layout() is not None
    finally:
        controller.close()
        window.close()
