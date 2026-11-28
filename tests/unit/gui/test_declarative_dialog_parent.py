"""导航页模式下的对话框父窗口（W6-A 收尾）。

## 缺陷

`_open_external_link` 与 `_choose_directory` 把 `self.dock` 当父窗口传给
`QMessageBox.question(...)` / `QFileDialog.getExistingDirectory(...)`。

W6-A 让 `preferred_zone: main` 走导航页，此时 **`self.dock` 是 `None`**。

Qt 接受 `None` 父窗口（不崩），但对话框会变成**独立顶层窗口**：

- 不居中于主窗口，出现在屏幕角落；
- 主窗口关闭时可能残留；
- 键盘焦点不进主窗口的 Tab 环。

## 修法

`_dialog_parent()`：dock → page → 主窗口，始终锚在用户正在看的窗口上。
"""

from __future__ import annotations

from typing import Any

import pytest
from PySide6 import QtWidgets

from omnicrawler.gui import declarative_view_host as dvh_module
from omnicrawler.gui.declarative_view_host import DeclarativeViewController


@pytest.fixture(scope="module")
def qapp() -> QtWidgets.QApplication:
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class _StubSurface:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    def close(self) -> None:
        pass


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
        "view_id": "g",
        "title": "G",
        "preferred_zone": zone,
        "movable": True,
        "resizable": True,
        "floatable": True,
        "default_width": 380,
        "default_height": 640,
        "minimum_width": 240,
        "minimum_height": 160,
        "components": [],
    }


def _window() -> QtWidgets.QMainWindow:
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
    return window


@pytest.fixture()
def no_media(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dvh_module, "MediaSurfaceService", _StubSurface)


def test_nav_page_dialog_parent_is_the_page_not_none(qapp, no_media) -> None:
    """★ 核心判据：导航页模式下父窗口**不能是 None**。

    传 None 不会崩，但对话框会脱离主窗口 —— 那正是这条要防的。
    """
    window = _window()
    controller = DeclarativeViewController(window, "g", _StubAdapter(_descriptor("main")))
    try:
        assert controller.dock is None, "前提：导航页模式没有 dock"
        parent = controller._dialog_parent()
        assert parent is not None, "对话框会变成独立顶层窗口"
        assert parent is controller.page
    finally:
        controller.close()
        window.close()


def test_dock_mode_dialog_parent_is_the_dock(qapp, no_media) -> None:
    """★ 反方向：侧栏面板模式仍锚在 dock 上（行为不变）。"""
    window = _window()
    controller = DeclarativeViewController(window, "p", _StubAdapter(_descriptor("right")))
    try:
        assert controller.dock is not None
        assert controller._dialog_parent() is controller.dock
    finally:
        controller.close()
        window.close()


def test_dialog_parent_falls_back_to_the_window(qapp, no_media) -> None:
    """dock 与 page 都不在时回退到主窗口（装配异常的兜底）。"""
    window = _window()
    controller = DeclarativeViewController(window, "p", _StubAdapter(_descriptor("right")))
    try:
        controller.dock = None
        controller.page = None
        assert controller._dialog_parent() is window
    finally:
        controller.close()
        window.close()


def test_external_link_uses_the_dialog_parent(qapp, no_media) -> None:
    """★ 端到端：`_open_external_link` 真的用 `_dialog_parent()`（不是 `self.dock`）。

    用一个不弹窗的替身接住 parent，确认拿到的不是 None。
    """
    window = _window()
    controller = DeclarativeViewController(window, "g", _StubAdapter(_descriptor("main")))
    captured: dict[str, Any] = {}

    class _Box:
        StandardButton = QtWidgets.QMessageBox.StandardButton

        @staticmethod
        def question(parent, *args, **kwargs):
            captured["parent"] = parent
            return QtWidgets.QMessageBox.StandardButton.No

    original = dvh_module.QtWidgets.QMessageBox
    try:
        dvh_module.QtWidgets.QMessageBox = _Box  # type: ignore[misc]
        controller._open_external_link("https://example.com", "example")
    finally:
        dvh_module.QtWidgets.QMessageBox = original  # type: ignore[misc]
        controller.close()
        window.close()
    assert "parent" in captured, "没有走到确认框"
    assert captured["parent"] is not None
    assert captured["parent"] is controller.page or captured["parent"] is window
