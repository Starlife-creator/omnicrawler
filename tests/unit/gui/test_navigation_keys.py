"""条目 6 第一步：导航身份改用稳定 key，行号由编排表派生。

**这一步是纯重构，行为必须完全不变。** 所以本文件的判据是两条：

1. **等价性**：``NavIndex.X`` 派生出的值与历史手写常量**逐项相同**
   （约 50 处调用点与既有测试都依赖这些值，任何一位偏移都是回归）；
2. **不再手抄**：导航增删后，行的身份不会因此错位——
   这正是条目 6 第二步（按能力条件构建导航）的前提。

为什么不能直接换成字符串 key 了事：``NavIndex`` 是对外契约
（``from omnicrawler.gui.main import NavIndex`` 在测试里就有）。
破坏它会让"纯重构"变成"跨模块改造"，那不是这一步的目的。
"""
from __future__ import annotations

import pytest

from omnicrawler.gui import navigation as nav

#: 历史手写常量。**这是本文件的黄金基准**，不得随实现改动。
_LEGACY = {
    "WORK_HEADER": 0,
    "HOME": 1,
    "WORKSPACE": 2,
    "WIZARD": 2,
    "MONITOR": 3,
    "RESULTS": 4,
    "AUTOMATION_HEADER": 5,
    "CHANGE_MONITOR": 6,
    "TOOLS_HEADER": 7,
    "PDF_WORKBENCH": 8,
    "CONVERT_TOOL": 9,
    "SCENE": 10,
    "LOGIN_SESSION": 11,
    "QML_SHOWCASE": 12,
    "ADVANCED_HEADER": 13,
    "YAML_EDITOR": 14,
    "EVIDENCE": 15,
    "PLUGIN_MARKET": 16,
    "DEVELOPER": 17,
}


def test_derived_navindex_matches_legacy_values_exactly() -> None:
    """派生值必须与历史手写常量逐项相同——纯重构不许有任何偏移。"""
    mismatches = [
        f"{name}: derived={getattr(nav.NavIndex, name)} legacy={expected}"
        for name, expected in _LEGACY.items()
        if getattr(nav.NavIndex, name) != expected
    ]
    assert mismatches == [], "导航行号发生偏移（纯重构不允许）：\n  " + "\n  ".join(mismatches)


def test_navindex_is_read_only() -> None:
    """NavIndex 是常量表：外部改写必须被拒绝。

    否则"派生"退化成"可漂移"——下一个人赋值改一行，其余全错位。
    """
    with pytest.raises(AttributeError):
        nav.NavIndex.HOME = 99  # type: ignore[misc]
    assert nav.NavIndex.HOME == _LEGACY["HOME"]


def test_headers_are_derived_from_group_entries() -> None:
    """分组标题集合必须等于编排表里声明的分组项，不是手抄的四个数字。"""
    expected = {
        nav.nav_rows()[entry.key]
        for entry in nav.NAV_LAYOUT
        if entry.is_header
    }
    assert set(nav.NavIndex.HEADERS) == expected


def test_every_selectable_entry_maps_to_a_stack_page() -> None:
    """每个可选导航项都必须能映射到一个真实栈页（否则点了没反应）。"""
    unknown = [
        entry.key for entry in nav.NAV_LAYOUT
        if entry.stack_key is not None and entry.stack_key not in nav.STACK_INDEX
    ]
    assert unknown == [], f"这些导航项指向不存在的栈页：{unknown}"


def test_stack_order_has_no_duplicates() -> None:
    """栈顺序不得重复——重复会让两行指向同一页（历史"错页"的成因）。"""
    assert len(set(nav.STACK_ORDER)) == len(nav.STACK_ORDER)


def test_row_to_stack_matches_historical_mapping() -> None:
    """行号→栈索引的映射必须与 main.py 里原来硬编码的对应关系一致。

    历史映射（从 ``_on_nav_changed`` 逐条读出，见下方注释）：
      HOME(1)→4, WORKSPACE(2)→0, MONITOR(3)→2, RESULTS(4)→3,
      CHANGE_MONITOR(6)→8, PDF_WORKBENCH(8)→6, CONVERT_TOOL(9)→7,
      SCENE(10)→11, LOGIN_SESSION(11)→12, QML_SHOWCASE(12)→13,
      YAML_EDITOR(14)→1, EVIDENCE(15)→5, PLUGIN_MARKET(16)→9,
      DEVELOPER(17)→10
    """
    expected = {
        1: 4, 2: 0, 3: 2, 4: 3,
        6: 8, 8: 6, 9: 7, 10: 11, 11: 12, 12: 13,
        14: 1, 15: 5, 16: 9, 17: 10,
    }
    assert nav.ROW_TO_STACK == expected


def test_inserting_an_entry_keeps_keys_stable() -> None:
    """核心收益：插入一项后，**已有 key 的行号可以被重算**，
    但 key 与栈页的绑定不会错——这是"身份不再依赖行号"的直接证明。
    """
    base = nav.nav_rows()
    workspace_row = base[nav.NavKey.WORKSPACE]

    # 插在 WORKSPACE **之前**（索引 workspace_row），使其后的所有行整体后移 1
    injected = nav.NavEntry("injected_probe", stack_key=nav.NavKey.HOME)
    extended = nav.NAV_LAYOUT[:workspace_row] + (injected,) + nav.NAV_LAYOUT[workspace_row:]

    rows = nav.nav_rows(extended)
    assert rows["injected_probe"] == workspace_row
    # WORKSPACE 及其后所有项的行号整体 +1，但 key 本身没变
    assert rows[nav.NavKey.WORKSPACE] == workspace_row + 1
    assert rows[nav.NavKey.DEVELOPER] == base[nav.NavKey.DEVELOPER] + 1
    # 栈绑定不受行号变化影响——这正是"身份稳定"的含义
    assert extended[workspace_row].stack_key == nav.NavKey.HOME


def test_navkey_values_are_unique() -> None:
    """NavKey 的常量值不得重复（复制粘贴常量时的典型事故）。"""
    values = [
        value for name, value in vars(nav.NavKey).items()
        if not name.startswith("_") and isinstance(value, str)
    ]
    assert len(set(values)) == len(values), "NavKey 存在重复值"


def test_layout_keys_are_unique() -> None:
    """编排表里不得有重复 key。"""
    keys = [entry.key for entry in nav.NAV_LAYOUT]
    assert len(set(keys)) == len(keys)
