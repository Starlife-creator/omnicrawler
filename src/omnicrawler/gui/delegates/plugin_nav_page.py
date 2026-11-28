"""插件**导航页**的挂载与卸载（W6-A）。

## 为什么需要它

声明式插件目前只能做**侧栏面板**（`QDockWidget`）。但市场里有几个插件本质上
是「一个完整页面」——社区指南、需求清单这类。把它们塞进 190px 宽的侧栏等于
装不下，而 GUI 侧没有任何通道能让插件贡献一个中央页。

## 硬规则（代码保证，不是约定）

1. **只能追加在末尾**：绝不插入到内置条目之前。`NavIndex` 是**行号常量**，
   由静态 `NAV_LAYOUT` 派生；插到前面会让所有内置行号错位 —— 那正是历史上
   「结果与复核错页」的成因。
2. **不能改动内置行**：本模块只 `addItem`，从不 `takeItem` 内置行。
   卸载时只移除自己登记过的 key。
3. **标签只是文本**：长度受限、不解释 HTML、不接受图标路径或样式表。
   侧栏固定 190px 宽，长标签会被截断 —— 与其让用户看半截字，不如拒绝。
4. **卸载必须成对清理**：导航行、栈页、映射表、以及空掉的「插件页面」分组
   标题，全部回收。漏掉任何一项都会留下一个点了就崩的入口。

## 与侧栏面板的关系

本模块只管**导航行 + 栈页**这一层；页面内容是宿主渲染的声明式组件（与侧栏面板
同一套 `COMPONENT_TYPES`）。所以「插件占一整页」不会带来新的绘制能力 ——
只是放置位置不同。
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6 import QtCore, QtWidgets

from ..i18n import _

LOGGER = logging.getLogger(__name__)

#: 「插件页面」分组的 key。以 `plugin:` 前缀与内置 `NavKey` 区分开，
#: 避免与未来内置条目撞名。
PLUGIN_NAV_GROUP_KEY = "plugin:__group__"

#: 侧栏标签长度上限。侧栏固定 190px；超过这个长度的标签一定会被截断。
MAX_NAV_LABEL_LENGTH = 24


def _nav(window: Any) -> QtWidgets.QListWidget | None:
    nav = getattr(window, "_nav", None)
    return nav if isinstance(nav, QtWidgets.QListWidget) else None


def _rebuild_maps(window: Any) -> None:
    """从侧栏**实际**内容重建映射表。

    ★ 必须整体重建而不是增量维护：增量维护要求「插入 / 删除 / 分组收回」三条
      路径各自更新正确，而它们彼此耦合（删掉分组标题会让后面所有行号 -1）。
      整体重建是 O(n)，n 是侧栏条目数（几十），代价可以忽略。
    """
    nav = _nav(window)
    if nav is None:  # pragma: no cover - 宿主装配异常
        return
    row_by_key: dict[str, int] = {}
    key_by_row: dict[int, str] = {}
    for row in range(nav.count()):
        item = nav.item(row)
        if item is None:  # pragma: no cover
            continue
        key = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if isinstance(key, str) and key:
            row_by_key[key] = row
            key_by_row[row] = key
    window._nav_row_by_key = row_by_key
    window._nav_key_by_row = key_by_row


def _ensure_group_header(window: Any) -> None:
    """按需在末尾创建「插件页面」分组标题。"""
    nav = _nav(window)
    if nav is None:  # pragma: no cover
        return
    row_by_key = getattr(window, "_nav_row_by_key", {}) or {}
    if PLUGIN_NAV_GROUP_KEY in row_by_key:
        return
    item = QtWidgets.QListWidgetItem(_("插件页面"))
    item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)  # 分组标题不可选
    item.setData(QtCore.Qt.ItemDataRole.UserRole, PLUGIN_NAV_GROUP_KEY)
    item.setData(
        QtCore.Qt.ItemDataRole.AccessibleDescriptionRole, _("导航分组")
    )
    nav.addItem(item)
    _rebuild_maps(window)


def _drop_group_header_if_empty(window: Any) -> None:
    """没有插件页面了就收回分组标题 —— 只剩标题的侧栏比少一行更糟。"""
    nav = _nav(window)
    if nav is None:  # pragma: no cover
        return
    row_by_key = getattr(window, "_nav_row_by_key", {}) or {}
    header_row = row_by_key.get(PLUGIN_NAV_GROUP_KEY)
    if header_row is None:
        return
    has_child = False
    for row in range(header_row + 1, nav.count()):
        item = nav.item(row)
        key = item.data(QtCore.Qt.ItemDataRole.UserRole) if item else None
        if isinstance(key, str) and key.startswith("plugin:"):
            has_child = True
            break
    if has_child:
        return
    nav.takeItem(header_row)
    _rebuild_maps(window)

def _sync_nav_pages(window: Any) -> None:
    """按当前侧栏行号重建 `_nav_pages`（row → 栈索引）。

    `_nav_pages` 的语义是「第几行 → 栈第几页」。插入 / 删除侧栏行之后，**两个
    数字都会变**，所以不能只改一个键 —— 必须按 key 重新映射一遍。

    插件页的 key → 页面对象存在 `_plugin_nav_pages` 里；内置条目仍查老表
    （它们的栈索引在内置装配时固定下来，行号变了要按新行号搬过去）。
    """
    nav = _nav(window)
    stack = getattr(window, "_stack", None)
    if nav is None or not isinstance(stack, QtWidgets.QStackedWidget):  # pragma: no cover
        return
    row_by_key = getattr(window, "_nav_row_by_key", {}) or {}
    plugin_pages = getattr(window, "_plugin_nav_pages", None)
    if not isinstance(plugin_pages, dict):
        plugin_pages = {}
        window._plugin_nav_pages = plugin_pages
    builtin = getattr(window, "_builtin_nav_page_by_key", None)
    if not isinstance(builtin, dict):
        builtin = {}
        window._builtin_nav_page_by_key = builtin

    pages: dict[int, int] = {}
    for key, row in row_by_key.items():
        widget = plugin_pages.get(key) or builtin.get(key)
        if widget is None:
            continue
        index = stack.indexOf(widget)
        if index >= 0:
            pages[row] = index
    window._nav_pages = pages


def register_plugin_nav_page(
    window: Any, key: str, label: str, page: QtWidgets.QWidget
) -> bool:
    """把插件页追加到侧栏末尾并在栈中挂载；返回是否成功。

    同名 key 已存在时**复用现有行**并替换栈页内容（插件重载路径），
    不叠出第二个入口。
    """
    nav = _nav(window)
    stack = getattr(window, "_stack", None)
    if nav is None or not isinstance(stack, QtWidgets.QStackedWidget):
        LOGGER.warning(_("宿主缺少导航栏或页面栈，插件页 %s 无法挂载"), key)
        return False
    if not str(key or "").strip():
        raise ValueError(_("插件页 key 不能为空"))
    text = str(label or "").strip()
    if not text:
        raise ValueError(_("插件页标签不能为空"))
    if len(text) > MAX_NAV_LABEL_LENGTH:
        raise ValueError(
            _(f"插件页标签最多 {MAX_NAV_LABEL_LENGTH} 个字符（侧栏只有 190px 宽）")
        )
    if "<" in text or ">" in text:
        raise ValueError(_("插件页标签不接受 HTML（侧栏按纯文本渲染）"))

    plugin_pages = getattr(window, "_plugin_nav_pages", None)
    if not isinstance(plugin_pages, dict):
        plugin_pages = {}
        window._plugin_nav_pages = plugin_pages

    _ensure_group_header(window)
    stack_index = stack.addWidget(page)
    del stack_index
    plugin_pages[key] = page
    _rebuild_maps(window)
    if key not in (getattr(window, "_nav_row_by_key", {}) or {}):
        item = QtWidgets.QListWidgetItem(text)
        item.setData(QtCore.Qt.ItemDataRole.UserRole, key)
        nav.addItem(item)
        _rebuild_maps(window)
    _sync_nav_pages(window)
    return True


def unregister_plugin_nav_page(window: Any, key: str) -> bool:
    """移除插件页的导航行与栈页；返回是否真的移除了什么。

    ★ 顺序要紧：**先**断开当前选中（避免删行时触发指向已删页的跳转），
      **再**从栈里摘掉页，**最后**删导航行。反过来会让用户看到一次空页闪烁。
    """
    nav = _nav(window)
    stack = getattr(window, "_stack", None)
    if nav is None:  # pragma: no cover
        return False
    row_by_key = getattr(window, "_nav_row_by_key", {}) or {}
    row = row_by_key.get(key)
    plugin_pages = getattr(window, "_plugin_nav_pages", None)
    if row is None and not (isinstance(plugin_pages, dict) and key in plugin_pages):
        return False

    pages = getattr(window, "_nav_pages", {}) or {}
    stack_index = pages.get(row) if row is not None else None
    # 若被删的正是当前页，先把选中移到第一行，避免删完停在空页上。
    if row is not None and nav.currentRow() == row and nav.count() > 1:
        nav.setCurrentRow(0)
    if isinstance(stack_index, int) and isinstance(stack, QtWidgets.QStackedWidget):
        widget = stack.widget(stack_index)
        if widget is not None:
            stack.removeWidget(widget)
            widget.deleteLater()
    if isinstance(plugin_pages, dict):
        plugin_pages.pop(key, None)
    if row is not None:
        nav.takeItem(row)
    _rebuild_maps(window)
    _drop_group_header_if_empty(window)
    _sync_nav_pages(window)
    return True


def adopt_builtin_nav_pages(window: Any) -> None:
    """把内置条目的「key → 页面对象」登记下来（供行号重排时重算）。

    内置装配完 `_nav_pages` 之后调用一次即可。之后再插插件行，
    `_sync_nav_pages` 就能把内置条目按新行号搬对位置 —— 否则插件页一插进来，
    所有内置行都会指向错页（那正是历史上「结果与复核错页」的形态）。
    """
    nav = _nav(window)
    stack = getattr(window, "_stack", None)
    if nav is None or not isinstance(stack, QtWidgets.QStackedWidget):  # pragma: no cover
        return
    row_by_key = getattr(window, "_nav_row_by_key", {}) or {}
    pages = getattr(window, "_nav_pages", {}) or {}
    builtin: dict[str, QtWidgets.QWidget] = {}
    for key, row in row_by_key.items():
        if str(key).startswith("plugin:"):
            continue
        index = pages.get(row)
        if not isinstance(index, int):
            continue
        widget = stack.widget(index)
        if widget is not None:
            builtin[key] = widget
    window._builtin_nav_page_by_key = builtin
