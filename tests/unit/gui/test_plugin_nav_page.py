"""W6-A：插件导航页（`preferred_zone: main`）。

## 为什么这块风险高

`NavIndex` 是**行号常量**，由静态 `NAV_LAYOUT` 派生。历史 bug「结果与复核错页」
就是行号错位造成的：把 `NavIndex.MONITOR` 当结果页用。

插件页要往同一个 `QListWidget` 里插行 —— 如果插到内置条目**前面**，或者插入后
不重算 `_nav_pages`（row → 栈索引），所有内置条目会指向错页。

所以这一组测试的核心不是「能不能加一行」，而是：**加了之后内置条目还对吗**。
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

QtCore = pytest.importorskip("PySide6.QtCore")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from omnicrawler.gui.delegates.plugin_nav_page import (  # noqa: E402
    MAX_NAV_LABEL_LENGTH,
    PLUGIN_NAV_GROUP_KEY,
    adopt_builtin_nav_pages,
    register_plugin_nav_page,
    unregister_plugin_nav_page,
)

ROLE = QtCore.Qt.ItemDataRole.UserRole


class _FakeWindow:
    """最小主窗口替身：只提供插件页真正用到的那几个属性。

    用替身而不是真 `MainWindow`：真窗口要装配整套页面（几秒），而这些判据
    只依赖 `_nav` / `_stack` / 三张映射表的结构契约。
    """

    def __init__(self) -> None:
        self._nav = QtWidgets.QListWidget()
        self._stack = QtWidgets.QStackedWidget()
        self._nav_pages: dict[int, int] = {}
        self._nav_row_by_key: dict[str, int] = {}
        self._nav_key_by_row: dict[int, str] = {}


@pytest.fixture()
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture()
def window(app):
    win = _FakeWindow()
    for key, label in (("home", "Home"), ("workspace", "Workspace")):
        win._stack.addWidget(QtWidgets.QWidget())
        item = QtWidgets.QListWidgetItem(label)
        item.setData(ROLE, key)
        win._nav.addItem(item)
    win._nav_pages = {0: 0, 1: 1}
    win._nav_row_by_key = {"home": 0, "workspace": 1}
    win._nav_key_by_row = {0: "home", 1: "workspace"}
    adopt_builtin_nav_pages(win)
    return win


# ── ① 内置行号绝不错位（本组最重要的一条）────────────────────────────


def test_builtin_rows_keep_their_pages_after_register(window) -> None:
    """★ 核心判据：插入插件页后，内置条目仍指向**原来那页**。

    这正是历史「结果与复核错页」的形态 —— 行号变了但映射没跟着变。
    """
    home_page = window._builtin_nav_page_by_key["home"]
    workspace_page = window._builtin_nav_page_by_key["workspace"]

    register_plugin_nav_page(window, "plugin:x:v1", "Guide", QtWidgets.QWidget())

    home_row = window._nav_row_by_key["home"]
    workspace_row = window._nav_row_by_key["workspace"]
    assert window._stack.widget(window._nav_pages[home_row]) is home_page
    assert window._stack.widget(window._nav_pages[workspace_row]) is workspace_page


def test_plugin_row_is_appended_after_all_builtin_rows(window) -> None:
    """★ 只能**追加在末尾**：插到前面会让所有内置行号错位。"""
    register_plugin_nav_page(window, "plugin:x:v1", "Guide", QtWidgets.QWidget())
    plugin_row = window._nav_row_by_key["plugin:x:v1"]
    assert plugin_row > window._nav_row_by_key["workspace"]
    assert plugin_row == window._nav.count() - 1


def test_group_header_only_shows_when_a_page_exists(window) -> None:
    """没有插件页时不该有「插件页面」空分组。"""
    assert PLUGIN_NAV_GROUP_KEY not in window._nav_row_by_key
    register_plugin_nav_page(window, "plugin:x:v1", "Guide", QtWidgets.QWidget())
    assert PLUGIN_NAV_GROUP_KEY in window._nav_row_by_key


def test_group_header_is_removed_when_last_page_unregisters(window) -> None:
    """★ 只剩标题的分组比少一行更糟。"""
    register_plugin_nav_page(window, "plugin:x:v1", "Guide", QtWidgets.QWidget())
    unregister_plugin_nav_page(window, "plugin:x:v1")
    assert PLUGIN_NAV_GROUP_KEY not in window._nav_row_by_key
    assert window._nav.count() == 2


def test_group_header_survives_while_another_page_remains(window) -> None:
    register_plugin_nav_page(window, "plugin:x:v1", "A", QtWidgets.QWidget())
    register_plugin_nav_page(window, "plugin:x:v2", "B", QtWidgets.QWidget())
    unregister_plugin_nav_page(window, "plugin:x:v1")
    assert PLUGIN_NAV_GROUP_KEY in window._nav_row_by_key
    assert "plugin:x:v2" in window._nav_row_by_key


# ── ② 标签只是文本 ────────────────────────────────────────────────────


def test_label_length_is_capped(window) -> None:
    """侧栏固定 190px，超长标签一定被截断 —— 拒绝比让用户看半截字好。"""
    with pytest.raises(ValueError, match="最多"):
        register_plugin_nav_page(
            window, "plugin:x:v1", "x" * (MAX_NAV_LABEL_LENGTH + 1),
            QtWidgets.QWidget(),
        )


def test_html_label_is_rejected(window) -> None:
    """★ 侧栏按纯文本渲染；接受 HTML 会让插件以为能用富文本。"""
    for bad in ("<b>Guide</b>", "a<br>b"):
        with pytest.raises(ValueError, match="不接受 HTML"):
            register_plugin_nav_page(
                window, "plugin:x:v1", bad, QtWidgets.QWidget(),
            )


def test_empty_label_is_rejected(window) -> None:
    with pytest.raises(ValueError, match="不能为空"):
        register_plugin_nav_page(window, "plugin:x:v1", "   ", QtWidgets.QWidget())


def test_empty_key_is_rejected(window) -> None:
    with pytest.raises(ValueError, match="key 不能为空"):
        register_plugin_nav_page(window, "", "Guide", QtWidgets.QWidget())


# ── ③ 卸载成对清理 ────────────────────────────────────────────────────


def test_unregister_removes_both_row_and_stack_page(window) -> None:
    """★ 漏掉任何一项都会留下一个点了就崩的入口。"""
    page = QtWidgets.QWidget()
    register_plugin_nav_page(window, "plugin:x:v1", "Guide", page)
    assert window._stack.indexOf(page) >= 0
    unregister_plugin_nav_page(window, "plugin:x:v1")
    assert "plugin:x:v1" not in window._nav_row_by_key
    assert window._stack.indexOf(page) < 0


def test_unregister_of_unknown_key_is_a_noop(window) -> None:
    assert unregister_plugin_nav_page(window, "plugin:nope") is False


def test_register_is_idempotent_for_the_same_key(window) -> None:
    """★ 插件重载不许叠出第二个入口。"""
    register_plugin_nav_page(window, "plugin:x:v1", "Guide", QtWidgets.QWidget())
    rows_after_first = window._nav.count()
    register_plugin_nav_page(window, "plugin:x:v1", "Guide", QtWidgets.QWidget())
    assert window._nav.count() == rows_after_first


def test_nav_pages_has_no_stale_rows_after_unregister(window) -> None:
    """★ 删除中间行之后，剩余映射必须整体重算（不是只删一个键）。"""
    register_plugin_nav_page(window, "plugin:x:v1", "A", QtWidgets.QWidget())
    register_plugin_nav_page(window, "plugin:x:v2", "B", QtWidgets.QWidget())
    unregister_plugin_nav_page(window, "plugin:x:v1")
    for row, index in window._nav_pages.items():
        assert window._stack.widget(index) is not None
        item = window._nav.item(row)
        assert item is not None
        key = item.data(ROLE)
        assert key, f"行 {row} 有映射但没有 key"
