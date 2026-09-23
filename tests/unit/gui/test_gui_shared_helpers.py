"""GUI 共享小工具必须**只有一处实现**（W6.7-① 的机器守卫）。

## 为什么

`_repolish_widget`（按 QSS 动态属性重刷控件外观）曾经在
`gui/views/convert_tool.py` 与 `gui/views/task_canvas_components.py` **各有一份逐字相同的实现**，
另有 view 反过来 `from .task_canvas_components import _repolish_widget`（跨 view 导入私有函数）。
三份形状、两处实现：改一处、忘一处就是"同一件事两个真源"。

现下沉到 `gui/design_system.py` 的公开 `repolish_widget`。本文件把它变成断言：
**函数名在 gui 包里只能定义一次，且必须定义在 design_system**。
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_GUI_ROOT = _REPO_ROOT / "src" / "omnicrawler" / "gui"

#: 必须唯一实现的共享小工具 → 允许定义它的模块（相对 gui 包的路径）
_SHARED_HELPERS: dict[str, str] = {
    "repolish_widget": "design_system.py",
    # V2：阴影的唯一构造入口（数值与取色都只在 design_system 出现一次）
    "shadow_effect": "design_system.py",
    # V2：列表/空态互斥同步（三个列表页共用一处实现）
    "sync_list_empty_state": "widgets/empty_state.py",
}

#: 阴影参数只能出现在这里（其余模块一律走 ``shadow_effect``）。
_SHADOW_TUNING_ATTRS = frozenset({"setBlurRadius", "setOffset"})
_SHADOW_TUNING_HOME = "design_system.py"


def _definitions(name: str) -> list[str]:
    """在 gui 包里找出所有定义 ``name`` 的模块（按 AST，不靠文本匹配）。"""
    found: list[str] = []
    for path in sorted(_GUI_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # 语法错误由别的门禁负责
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
                found.append(path.relative_to(_GUI_ROOT).as_posix())
    return found


def test_shared_helpers_are_defined_exactly_once() -> None:
    """每个共享小工具在 gui 包里**只能有一处定义**，且位置就是约定的那个模块。"""
    for name, expected in _SHARED_HELPERS.items():
        definitions = _definitions(name)
        assert definitions, f"gui 包里找不到 {name!r} 的定义（是否被误删？）"
        assert definitions == [expected], (
            f"{name!r} 应只在 {expected} 定义一处，实际出现在：{definitions}。"
            f"重复实现是「同一件事两个真源」——请下沉到 {expected} 再复用。"
        )


def test_probe_is_not_vacuous() -> None:
    """守卫要有意义：确认真的扫到了 gui 源码（否则是空集对空集的假通过）。"""
    files = [p for p in _GUI_ROOT.rglob("*.py") if "__pycache__" not in p.parts]
    assert len(files) > 50, f"只扫到 {len(files)} 个 gui 模块，路径推断可能写错了"


def _shadow_tuning_sites() -> list[str]:
    """找出直接调 ``setBlurRadius`` / ``setOffset`` 的模块（按 AST，不靠文本匹配）。"""
    found: list[str] = []
    for path in sorted(_GUI_ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _SHADOW_TUNING_ATTRS:
                found.append(path.relative_to(_GUI_ROOT).as_posix())
                break
    return found


def test_shadow_tuning_only_happens_in_design_system() -> None:
    """V2：阴影参数**只能**在 design_system 里出现。

    反例（收敛前的真实形态）：`home.py` 写 blur 24 / offset 0,7，
    `widgets/toast.py` 写 blur 16 / offset 0,4 —— 同一套设计体系两处硬编码，
    调层级或换令牌时必然漏改一处。现在调用方只声明"哪一层"。
    """
    assert _shadow_tuning_sites() == [_SHADOW_TUNING_HOME], (
        "阴影参数（setBlurRadius/setOffset）只允许出现在 "
        f"{_SHADOW_TUNING_HOME}，实际出现在：{_shadow_tuning_sites()}。"
        "请改用 design_system.shadow_effect(widget, level=…)。"
    )
