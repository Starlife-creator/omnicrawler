"""条目 6 第二步：导航按**能力就绪状态**条件生成。

判据来自产品决定（见《候选优化》条目 6 的风险栏）：

> 用户不应看到用不了的界面。组件未安装 ⇒ 该入口不显示。

这条判据**凌驾于体积考虑**——一个点进去空空、或只提示"需要安装组件"的工作台，
比完全不出现更糟：它既不美观，也违背精简主旨。

本文件钉住四件事：
1. 能力缺失时入口**不出现**（而不是出现后降级）；
2. **缺失的 key 视为未就绪**（保守默认：宁可少显示）；
3. **空分组标题被去掉**（只剩标题的侧栏比少一行更糟）；
4. `QML_SHOWCASE` 在冻结包里不显示——这是历史上真实存在的死页面。
"""
from __future__ import annotations

import pytest

from omnicrawler.gui import navigation as nav

#: 全部能力就绪（用于确认"过滤不会误删"）。
_ALL_READY = {"pdf": True, "qml": True}
#: 全部能力缺失（用于确认"该藏的确实藏了"）。
_NONE_READY: dict[str, bool] = {}


def _keys(ready: dict[str, bool]) -> set[str]:
    return {entry.key for entry in nav.visible_entries(ready)}


def test_all_ready_shows_every_selectable_entry() -> None:
    """能力齐全时不得少显示任何可选入口（过滤不能变成误删）。"""
    expected = {entry.key for entry in nav.NAV_LAYOUT if not entry.is_header}
    actual = _keys(_ALL_READY)
    # 分组标题不属于"可选入口"，需从比较中排除；但仍应保留在可见集里。
    actual_selectable = {
        key for key in actual
        if not key.startswith("group:")
    }
    assert actual_selectable == expected
    assert {key for key in actual if key.startswith("group:")} == {
        entry.key for entry in nav.NAV_LAYOUT if entry.is_header
    }, "能力齐全时所有分组标题都应保留"


def test_missing_pdf_hides_pdf_and_convert_tool() -> None:
    """未装 PDF 栈 ⇒ 文档工作台与格式互转**都不显示**。"""
    keys = _keys(_NONE_READY)
    assert nav.NavKey.PDF_WORKBENCH not in keys, (
        "未装 PDF 却显示 文档工作台——用户点进去会看到一个空工作台"
    )
    assert nav.NavKey.CONVERT_TOOL not in keys, "格式互转同样依赖 PDF 栈"


def test_missing_qml_hides_the_qml_showcase() -> None:
    """QML 运行时缺失 ⇒ 橱窗入口不显示。

    这是历史死页面的直接修复：`QML_SHOWCASE` 在冻结包里被 spec 排除
    （QtQml/QtQuick 不打包），但导航里那一行原来**始终显示**，点了只会降级。
    """
    keys = _keys(_NONE_READY)
    assert nav.NavKey.QML_SHOWCASE not in keys
    # 且它没有拖累其它项
    assert nav.NavKey.HOME in keys and nav.NavKey.PLUGIN_MARKET in keys


def test_absent_capability_key_counts_as_not_ready() -> None:
    """能力表里**缺失**的 key 必须视为未就绪（保守默认）。

    反例风险：若把缺失当作"就绪"，任何探测失败都会让入口错误地出现。
    """
    assert nav.NavKey.PDF_WORKBENCH not in _keys({})
    assert nav.NavKey.PDF_WORKBENCH not in _keys({"qml": True})


def test_empty_group_header_is_dropped() -> None:
    """分组标题后面没有可见项时必须去掉，避免只剩标题的空分组。"""
    # 构造一个只有 PDF 项的分组：PDF 缺失后该分组应整组消失
    layout = (
        nav.NavEntry(nav.NavKey.GROUP_TOOLS),
        nav.NavEntry(nav.NavKey.PDF_WORKBENCH, stack_key=nav.NavKey.PDF_WORKBENCH,
                     requires=("pdf",)),
        nav.NavEntry(nav.NavKey.GROUP_ADVANCED),
        nav.NavEntry(nav.NavKey.PLUGIN_MARKET, stack_key=nav.NavKey.PLUGIN_MARKET),
    )
    visible = nav.visible_entries({}, layout)
    keys = [entry.key for entry in visible]
    assert nav.NavKey.GROUP_TOOLS not in keys, "工具分组已无可见项，应整组去掉"
    assert nav.NavKey.GROUP_ADVANCED in keys, "高级分组仍有插件市场，必须保留"


def test_group_header_kept_when_it_has_visible_children() -> None:
    """分组里还有可见项时，标题必须保留（否则等于误删分组结构）。"""
    keys = _keys(_ALL_READY)
    assert nav.NavKey.GROUP_TOOLS in keys
    assert nav.NavKey.GROUP_WORK in keys


def test_capability_keys_come_from_layout() -> None:
    """能力 key 列表必须由编排表派生，不能手写（否则增删入口时会漂移）。"""
    expected: list[str] = []
    for entry in nav.NAV_LAYOUT:
        for name in entry.requires or ():
            if name not in expected:
                expected.append(name)
    assert list(nav.capability_keys()) == expected
    assert "pdf" in expected


def test_visible_entries_preserves_layout_order() -> None:
    """过滤必须保持编排表顺序（侧栏顺序是产品决定，不是实现细节）。"""
    full = [entry.key for entry in nav.visible_entries(_ALL_READY)]
    partial = [entry.key for entry in nav.visible_entries({"pdf": True})]
    # 部分可见集必须是完整顺序的**子序列**
    indices = [full.index(key) for key in partial]
    assert indices == sorted(indices)


def test_multiple_requirements_are_all_required_not_any() -> None:
    """``requires`` 有多个能力时，必须**全部**就绪才显示（不是任一）。

    这条守卫来自一次反向断言实测：把 ``all`` 改成 ``any`` 时，
    原有测试全部仍然通过——说明那个缺口当时没有被钉住。
    多条件项在真实编排表里暂未出现，但它是协议的一部分，
    一旦有人写出 ``requires=("pdf", "ocr")``，``any`` 会让装了半个的入口出现。
    """
    layout = (
        nav.NavEntry("probe", stack_key=nav.NavKey.PDF_WORKBENCH,
                     requires=("pdf", "qml")),
    )
    # 只有其中一个就绪 ⇒ 不显示
    assert nav.visible_entries({"pdf": True}, layout) == ()
    assert nav.visible_entries({"qml": True}, layout) == ()
    # 全部就绪 ⇒ 显示
    assert len(nav.visible_entries({"pdf": True, "qml": True}, layout)) == 1


def test_navigation_capabilities_never_raises() -> None:
    """能力探测不得抛异常——它在 GUI 构建路径上，失败会让窗口起不来。"""
    from omnicrawler.core.capabilities import navigation_capabilities

    ready = navigation_capabilities()
    assert set(ready) == set(nav.capability_keys())
    assert all(isinstance(value, bool) for value in ready.values())


@pytest.mark.parametrize("key", ["pdf", "qml"])
def test_required_keys_are_real_capability_names(key: str) -> None:
    """编排表里的能力 key 必须被探测函数实现（拼错会让入口永久消失）。"""
    from omnicrawler.core.capabilities import navigation_capabilities

    assert key in navigation_capabilities()
