"""主导航的**稳定标识**与派生索引（S3.1.2/15，条目 6 第一步）。

为什么需要这个模块
------------------
历史实现用 ``NavIndex`` 的**行号常量**同时表达两件不同的事：

* 侧栏 ``QListWidget`` 的**行号**（``setCurrentRow`` 用）；
* 页面栈 ``QStackedWidget`` 的**索引**（``show`` 用）。

两者是**独立编号**，而且都不稳定：往侧栏插一行、往栈里加一页，
另一侧的数字就全错位。真实后果是历史 bug「结果与复核错页」
（原实现把 ``NavIndex.MONITOR`` 当结果页用）。

本模块的解决方式（**增量式，不破坏既有契约**）
----------------------------------------------
1. 导航项与栈页改用**字符串 key**（``NavKey``）标识身份；
2. ``NAV_LAYOUT`` 是导航编排的**唯一真源**：每项声明 key 与对应栈页；
3. ``NavIndex`` 的属性值**由这张表派生**，不再是手写常量。

**本模块不存中文标签**：标签属于展示层，由 ``central_area`` 用 ``_()`` 包裹后
按 key 取用（否则 i18n 门禁会正确地判红——UI 文案必须经过翻译）。
本模块同样不依赖 Qt，因此可在无 Qt 环境下被测试与工具直接引用。
"""
from __future__ import annotations

from dataclasses import dataclass


class NavKey:
    """导航项与栈页的稳定标识。

    值一旦发布就**不要改名**：它可能出现在测试、插件与用户脚本里。
    """

    # ── 分组标题（不可选中）──
    GROUP_WORK = "group:work"
    GROUP_AUTOMATION = "group:automation"
    GROUP_TOOLS = "group:tools"
    GROUP_ADVANCED = "group:advanced"

    # ── 工作 ──
    HOME = "home"
    WORKSPACE = "workspace"
    MONITOR = "monitor"
    RESULTS = "results"

    # ── 自动化 ──
    CHANGE_MONITOR = "change_monitor"

    # ── 工具 ──
    PDF_WORKBENCH = "pdf_workbench"
    CONVERT_TOOL = "convert_tool"
    SCENE = "scene"
    LOGIN_SESSION = "login_session"
    QML_SHOWCASE = "qml_showcase"

    # ── 高级 ──
    YAML_EDITOR = "yaml_editor"
    EVIDENCE = "evidence"
    PLUGIN_MARKET = "plugin_market"
    DEVELOPER = "developer"


@dataclass(frozen=True, slots=True)
class NavEntry:
    """一条导航项的声明。

    ``stack_key`` 为 ``None`` 表示分组标题（不可选、不映射栈页）。

    ``requires`` 是**能力条件**（条目 6 第二步）：值为能力检查的 key 组合
    （如 ``("pdf",)``），全部就绪才显示该项。``None`` 表示无条件显示。

    为什么把条件写在编排表里而不是散在展示层：**"装了什么就看到什么"是产品判据**
    （见《候选优化》条目 6），把它和导航顺序放在同一张表里，才不会出现
    "删了功能但忘了删入口"这种残留在 UI 上的死页面。
    """

    key: str
    stack_key: str | None = None
    requires: tuple[str, ...] | None = None

    @property
    def is_header(self) -> bool:
        return self.stack_key is None


#: **导航编排的唯一真源**。顺序即侧栏显示顺序；标签在展示层按 key 取。
NAV_LAYOUT: tuple[NavEntry, ...] = (
    NavEntry(NavKey.GROUP_WORK),
    NavEntry(NavKey.HOME, stack_key=NavKey.HOME),
    NavEntry(NavKey.WORKSPACE, stack_key=NavKey.WORKSPACE),
    NavEntry(NavKey.MONITOR, stack_key=NavKey.MONITOR),
    NavEntry(NavKey.RESULTS, stack_key=NavKey.RESULTS),
    NavEntry(NavKey.GROUP_AUTOMATION),
    NavEntry(NavKey.CHANGE_MONITOR, stack_key=NavKey.CHANGE_MONITOR),
    NavEntry(NavKey.GROUP_TOOLS),
    # PDF 工作台与格式互转依赖 PDF 栈；未装时整个入口不显示，
    # 而不是让用户点进去看到一个空工作台（条目 6 的核心判据）。
    NavEntry(NavKey.PDF_WORKBENCH, stack_key=NavKey.PDF_WORKBENCH, requires=("pdf",)),
    NavEntry(NavKey.CONVERT_TOOL, stack_key=NavKey.CONVERT_TOOL, requires=("pdf",)),
    NavEntry(NavKey.SCENE, stack_key=NavKey.SCENE),
    NavEntry(NavKey.LOGIN_SESSION, stack_key=NavKey.LOGIN_SESSION),
    # QML 橱窗在冻结包里已被 spec 排除（QtQml/QtQuick 不打包），
    # 保留代码但**不显示入口**——这正是历史上那个"点了只会看到降级提示"的死页面。
    NavEntry(NavKey.QML_SHOWCASE, stack_key=NavKey.QML_SHOWCASE, requires=("qml",)),
    NavEntry(NavKey.GROUP_ADVANCED),
    NavEntry(NavKey.YAML_EDITOR, stack_key=NavKey.YAML_EDITOR),
    NavEntry(NavKey.EVIDENCE, stack_key=NavKey.EVIDENCE),
    NavEntry(NavKey.PLUGIN_MARKET, stack_key=NavKey.PLUGIN_MARKET),
    NavEntry(NavKey.DEVELOPER, stack_key=NavKey.DEVELOPER),
)


def visible_entries(
    ready: dict[str, bool],
    layout: tuple[NavEntry, ...] = NAV_LAYOUT,
) -> tuple[NavEntry, ...]:
    """按能力就绪状态过滤导航项（条目 6 第二步）。

    ``ready`` 是「能力 key → 是否就绪」；**缺失的 key 视为未就绪**
    （而非默认显示）：宁可少显示一个入口，也不要让用户点进空页面。

    分组标题的处理：**只有当它后面还有至少一个可见项时才保留**，
    避免出现一个空分组（只剩标题的侧栏比少一行更糟）。
    """
    selected: list[NavEntry] = []
    for entry in layout:
        if entry.requires is None:
            selected.append(entry)
            continue
        if all(ready.get(name, False) for name in entry.requires):
            selected.append(entry)

    # 去掉"后面没有可见项"的分组标题。
    # ★ 关键：只在**到下一个分组标题之前**找子项——否则空分组会借用
    #   后面分组的项，导致"工具"分组明明全被过滤掉却仍然留在侧栏。
    result: list[NavEntry] = []
    for index, entry in enumerate(selected):
        if not entry.is_header:
            result.append(entry)
            continue
        has_child = False
        for following in selected[index + 1:]:
            if following.is_header:
                break  # 进入下一分组，本组到此为止
            has_child = True
            break
        if has_child:
            result.append(entry)
    return tuple(result)


def capability_keys(layout: tuple[NavEntry, ...] = NAV_LAYOUT) -> tuple[str, ...]:
    """编排表用到的全部能力 key（去重、稳定顺序）。

    供展示层一次性查询能力状态，避免在循环里反复探测。
    """
    seen: list[str] = []
    for entry in layout:
        for name in entry.requires or ():
            if name not in seen:
                seen.append(name)
    return tuple(seen)


def nav_rows(layout: tuple[NavEntry, ...] = NAV_LAYOUT) -> dict[str, int]:
    """``key → 行号``（由编排表派生，不手写）。"""
    return {entry.key: row for row, entry in enumerate(layout)}


#: 页面栈的**实际**顺序。必须与 ``central_area`` 里 ``addWidget`` 的调用顺序一致。
#: 单独声明而不是从 NAV_LAYOUT 推导：栈顺序与导航顺序是两个独立事实
#: （历史上首页在栈第 5 位、工作台在栈第 1 位）。
STACK_ORDER: tuple[str, ...] = (
    NavKey.WORKSPACE,
    NavKey.YAML_EDITOR,
    NavKey.MONITOR,
    NavKey.RESULTS,
    NavKey.HOME,
    NavKey.EVIDENCE,
    NavKey.PDF_WORKBENCH,
    NavKey.CONVERT_TOOL,
    NavKey.CHANGE_MONITOR,
    NavKey.PLUGIN_MARKET,
    NavKey.DEVELOPER,
    NavKey.SCENE,
    NavKey.LOGIN_SESSION,
    NavKey.QML_SHOWCASE,
)


def build_stack_index(order: tuple[str, ...] = STACK_ORDER) -> dict[str, int]:
    """``key → 栈索引``（由顺序表派生）。"""
    return {key: index for index, key in enumerate(order)}


STACK_INDEX: dict[str, int] = build_stack_index()


class _ReadOnlyMeta(type):
    """让 ``NavIndex`` 的类属性在初始化完成后不可改写。

    为什么需要：这些值是**派生**出来的。若允许外部 ``NavIndex.HOME = 99``，
    "派生"立刻退化成"可漂移"，下一个人又会拿到错页。
    类级 ``__setattr__`` 只对实例生效，所以必须用元类拦在类型层。
    """

    def __setattr__(cls, name: str, value: object) -> None:
        if getattr(cls, "__frozen__", False):
            # 文案保持 ASCII：本模块不依赖 i18n（GUI 源码的未包裹中文会被门禁判红）。
            raise AttributeError(f"NavIndex.{name} is a read-only constant")
        type.__setattr__(cls, name, value)


class NavIndex(metaclass=_ReadOnlyMeta):
    """主导航**行号**常量（0-based，由 ``NAV_LAYOUT`` 派生）。

    保留这个类是为了兼容既有调用方与测试（约 50 处引用 ``NavIndex.X``）。
    **新代码请用 ``NavKey``**：行号会随导航增删而变，key 不会。

    取值现在由 ``nav_rows()`` 派生，不再是手抄——因此"与 nav_items 顺序一致"
    这条脆弱约定由代码保证，而不是靠注释提醒。

    下方注解只是**给静态检查器的声明**；真实取值在模块尾部由 ``_ATTR_TO_KEY``
    统一注入（注入完成后上锁，见 ``__frozen__``）。注解与注入若不一致，
    ``test_derived_navindex_matches_legacy_values_exactly`` 会当场判红。
    """

    WORK_HEADER: int
    HOME: int
    WORKSPACE: int
    WIZARD: int
    MONITOR: int
    RESULTS: int
    AUTOMATION_HEADER: int
    CHANGE_MONITOR: int
    TOOLS_HEADER: int
    PDF_WORKBENCH: int
    CONVERT_TOOL: int
    SCENE: int
    LOGIN_SESSION: int
    QML_SHOWCASE: int
    ADVANCED_HEADER: int
    YAML_EDITOR: int
    EVIDENCE: int
    PLUGIN_MARKET: int
    DEVELOPER: int
    HEADERS: frozenset[int]


#: key → 行号（派生）。
_ROW_BY_KEY = nav_rows()

#: 常量名 → key。名字与历史实现保持一致，便于平滑迁移。
_ATTR_TO_KEY: dict[str, str] = {
    "WORK_HEADER": NavKey.GROUP_WORK,
    "HOME": NavKey.HOME,
    "WORKSPACE": NavKey.WORKSPACE,
    "WIZARD": NavKey.WORKSPACE,  # 向后兼容别名
    "MONITOR": NavKey.MONITOR,
    "RESULTS": NavKey.RESULTS,
    "AUTOMATION_HEADER": NavKey.GROUP_AUTOMATION,
    "CHANGE_MONITOR": NavKey.CHANGE_MONITOR,
    "TOOLS_HEADER": NavKey.GROUP_TOOLS,
    "PDF_WORKBENCH": NavKey.PDF_WORKBENCH,
    "CONVERT_TOOL": NavKey.CONVERT_TOOL,
    "SCENE": NavKey.SCENE,
    "LOGIN_SESSION": NavKey.LOGIN_SESSION,
    "QML_SHOWCASE": NavKey.QML_SHOWCASE,
    "ADVANCED_HEADER": NavKey.GROUP_ADVANCED,
    "YAML_EDITOR": NavKey.YAML_EDITOR,
    "EVIDENCE": NavKey.EVIDENCE,
    "PLUGIN_MARKET": NavKey.PLUGIN_MARKET,
    "DEVELOPER": NavKey.DEVELOPER,
}

for _attr, _key in _ATTR_TO_KEY.items():
    setattr(NavIndex, _attr, _ROW_BY_KEY[_key])

#: 分组标题的行号集合（派生）。
NavIndex.HEADERS = frozenset(
    _ROW_BY_KEY[key]
    for key in (NavKey.GROUP_WORK, NavKey.GROUP_AUTOMATION,
                NavKey.GROUP_TOOLS, NavKey.GROUP_ADVANCED)
)

#: 派生完成 → 上锁。此后任何 ``NavIndex.X = ...`` 都会抛 AttributeError。
NavIndex.__frozen__ = True

#: 行号 → 栈索引（供 ``_on_nav_changed`` 使用，替代手写映射）。
ROW_TO_STACK: dict[int, int] = {
    _ROW_BY_KEY[entry.key]: STACK_INDEX[entry.stack_key]
    for entry in NAV_LAYOUT
    if entry.stack_key is not None
}

#: 行号 → key（反向查询）。
ROW_TO_KEY: dict[int, str] = {row: key for key, row in _ROW_BY_KEY.items()}
