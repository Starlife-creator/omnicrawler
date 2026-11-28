"""W6-A 补口：插件页必须能出现在命令面板（Ctrl+K）里（2026-11-28）。

## 洞在哪

`build_palette_commands()` 遍历的是 `visible_entries(navigation_capabilities())`
—— 那张表来自**静态** `NAV_LAYOUT`。插件页是运行期加进侧栏的，不在里面。

于是「侧栏有、Ctrl+K 搜不到」。而 Ctrl+K 恰恰是重度用户最常用的入口。

## 判据来源

从**实际侧栏**读 `(key, 标签)`，而不是从注册表读：侧栏是用户看到的唯一真源。
注册表可能领先于（已注册但还没加行）或落后于（已卸载但还没清）侧栏，
读侧栏永远不会与用户看到的不一致。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from omnicrawler.gui.command_palette import build_palette_commands  # noqa: E402
from omnicrawler.gui.delegates.plugin_nav_page import (  # noqa: E402
    PLUGIN_NAV_GROUP_KEY,
    adopt_builtin_nav_pages,
    register_plugin_nav_page,
    unregister_plugin_nav_page,
)

ROLE = QtCore.Qt.ItemDataRole.UserRole


@pytest.fixture()
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _window() -> QtWidgets.QMainWindow:
    """最小宿主：`_nav` + `_stack` + 映射表 + 一条内置页。"""
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
    home = QtWidgets.QWidget()
    stack.addWidget(home)
    item = QtWidgets.QListWidgetItem("Home")
    item.setData(ROLE, "home")
    nav.addItem(item)
    window._nav_pages = {0: 0}
    window._nav_row_by_key = {"home": 0}
    window._nav_key_by_row = {0: "home"}
    adopt_builtin_nav_pages(window)
    return window


def _plugin_commands(window) -> list:
    return [
        command
        for command in build_palette_commands(window, capabilities={})
        if command.command_id.startswith("nav:plugin:")
    ]


def test_plugin_page_appears_in_the_palette(app) -> None:
    """★ 核心判据：插件页在侧栏里，就必须也在 Ctrl+K 里。"""
    window = _window()
    try:
        register_plugin_nav_page(window, "plugin:guide:v1", "Community Guide", QtWidgets.QWidget())
        commands = _plugin_commands(window)
        assert len(commands) == 1, f"插件页没进命令面板：{[c.title for c in commands]}"
        assert commands[0].title == "Community Guide"
    finally:
        window.close()


def test_plugin_page_is_gone_from_the_palette_after_unregister(app) -> None:
    """★ 反方向：卸载后命令面板不许还留着一个点了没反应的条目。"""
    window = _window()
    try:
        register_plugin_nav_page(window, "plugin:guide:v1", "Community Guide", QtWidgets.QWidget())
        unregister_plugin_nav_page(window, "plugin:guide:v1")
        assert _plugin_commands(window) == []
    finally:
        window.close()


def test_group_header_is_not_a_palette_command(app) -> None:
    """分组标题不可选中，列进命令面板只会得到点了没反应的条目。"""
    window = _window()
    try:
        register_plugin_nav_page(window, "plugin:guide:v1", "Community Guide", QtWidgets.QWidget())
        assert PLUGIN_NAV_GROUP_KEY in window._nav_row_by_key
        command_ids = {c.command_id for c in _plugin_commands(window)}
        assert f"nav:{PLUGIN_NAV_GROUP_KEY}" not in command_ids
    finally:
        window.close()


def test_palette_command_group_is_the_plugin_group(app) -> None:
    """★ 分类要落在「插件页面」组里，而不是混进内置页面的组。"""
    window = _window()
    try:
        register_plugin_nav_page(window, "plugin:guide:v1", "Community Guide", QtWidgets.QWidget())
        command = _plugin_commands(window)[0]
        assert command.category.endswith("插件页面"), command.category
    finally:
        window.close()


def test_palette_reads_the_sidebar_not_the_registry(app) -> None:
    """★ 判据来源是**侧栏实际内容**。

    手工往侧栏塞一行（不经过注册函数）也必须能被面板发现 —— 这证明读的是
    侧栏而不是某个内部注册表。
    """
    window = _window()
    try:
        item = QtWidgets.QListWidgetItem("Hand Made")
        item.setData(ROLE, "plugin:manual:v9")
        window._nav.addItem(item)
        titles = {c.title for c in _plugin_commands(window)}
        assert "Hand Made" in titles, f"只读了注册表，没读侧栏：{titles}"
    finally:
        window.close()


def test_multiple_plugin_pages_all_appear(app) -> None:
    window = _window()
    try:
        register_plugin_nav_page(window, "plugin:a:v1", "Alpha", QtWidgets.QWidget())
        register_plugin_nav_page(window, "plugin:b:v1", "Beta", QtWidgets.QWidget())
        titles = {c.title for c in _plugin_commands(window)}
        assert titles == {"Alpha", "Beta"}
    finally:
        window.close()
