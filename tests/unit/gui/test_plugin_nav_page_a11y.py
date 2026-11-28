"""插件导航页的无障碍名（W6-A 补口，2026-11-28）。

## 缺陷

`preferred_zone: main` 的插件页是一个普通 `QWidget`。实测它的
`accessibleName()` 返回**空串** —— 读屏器会把整页念成「未命名控件」。

而所有内置页面都在自己的 `__init__` 里设了（`_home` → 「OmniCrawler 首页」、
`_yaml_editor` → 「YAML 编辑器」、`chart_view` → 「字段完整度图表」…）。
插件页此前是**唯一漏掉的**。

## 判据

无障碍名用描述符里的 `title`（插件作者已经给了）而不是拼 `plugin_id` ——
用户看到的就是那个词。
"""

from __future__ import annotations

from typing import Any

import pytest
from PySide6 import QtCore, QtWidgets

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


def _descriptor(title: str) -> dict[str, Any]:
    return {
        "view_id": "guide",
        "title": title,
        "preferred_zone": "main",
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


def test_nav_page_has_an_accessible_name(qapp, no_media) -> None:
    """★ 核心判据：导航页必须有非空 `accessibleName`。

    空串会让读屏器把整页念成「未命名控件」。
    """
    window = _window()
    controller = DeclarativeViewController(
        window, "guide", _StubAdapter(_descriptor("Community Guide"))
    )
    try:
        assert controller.page is not None
        name = controller.page.accessibleName()
        assert name.strip(), "插件导航页没有无障碍名"
        assert name == "Community Guide", f"应该用描述符 title，实际是 {name!r}"
    finally:
        controller.close()
        window.close()


def test_accessible_name_follows_the_descriptor_title(qapp, no_media) -> None:
    """★ 无障碍名必须来自描述符 —— 换了 title 就得跟着变。"""
    window = _window()
    controller = DeclarativeViewController(
        window, "guide", _StubAdapter(_descriptor("需求清单"))
    )
    try:
        assert controller.page is not None
        assert controller.page.accessibleName() == "需求清单"
    finally:
        controller.close()
        window.close()


def test_nav_item_is_keyboard_selectable(qapp, no_media) -> None:
    """★ 键盘可达性：插件页的导航项必须可选中、可启用。

    分组标题用的是 `NoItemFlags`（不可选中）；插件页**不能**沿用那一套，
    否则键盘用户永远进不去。
    """
    window = _window()
    controller = DeclarativeViewController(
        window, "guide", _StubAdapter(_descriptor("Guide"))
    )
    try:
        row = window._nav_row_by_key.get(controller._nav_key)
        assert row is not None, "导航行没登记"
        item = window._nav.item(row)
        assert item is not None
        assert item.flags() & QtCore.Qt.ItemFlag.ItemIsSelectable
        assert item.flags() & QtCore.Qt.ItemFlag.ItemIsEnabled
        assert item.text() == "Guide"
    finally:
        controller.close()
        window.close()


def test_navigating_to_the_page_shows_it(qapp, no_media) -> None:
    """★ 选中该行后页面栈真的切过去（不是只加了个入口）。"""
    window = _window()
    controller = DeclarativeViewController(
        window, "guide", _StubAdapter(_descriptor("Guide"))
    )
    try:
        row = window._nav_row_by_key[controller._nav_key]
        window._nav.setCurrentRow(row)
        qapp.processEvents()
        assert window._stack.currentWidget() is controller.page
    finally:
        controller.close()
        window.close()
