"""每个导航页都必须有 `AccessibleName`（2026-11-28）。

## 缺陷

`QListWidget` 的选中项与 `QStackedWidget` 的当前页是两个独立对象。读屏器切页时
念的是**页面**的 `accessibleName`；空串会念成「未命名控件」，用户不知道自己在哪。

实测（真 `MainWindow`，修补前）：

```
内置导航页缺 AccessibleName:
  [("workspace", "QWidget"), ("monitor", "QWidget"), ("results", "QWidget")]
```

三个都是 `central_area` 里临时 `QWidget()` 拼出来的容器，没有自己的 `__init__`，
所以「在类里设」这条惯例覆盖不到它们。

## 为什么用 AST 而不是构造真 `MainWindow`

真 `MainWindow()` 在本测试环境里会挂起（它启动时弹首启引导 / 探测资源），
实测把 `tests/unit/gui` 整个卡住。而这条判据的本质是「这三行代码存在且成对」，
AST 足够精确 —— 它读的是**赋值语句与实际调用**，不是正则猜文本。

运行时行为另有一条轻量测试（`test_plugin_nav_page_a11y.py`）用最小替身窗口覆盖。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_CENTRAL = _REPO / "src" / "omnicrawler" / "gui" / "delegates" / "central_area.py"

#: 三个容器页的变量名 → 期望的无障碍名（与 `_NAV_LABELS` 的措辞一致）。
_CONTAINERS = {
    "mw._workspace_widget": "任务工作台",
    "monitor_widget": "运行与历史",
    "results_widget": "结果与复核",
}


def _accessible_names() -> dict[str, str]:
    """收集 `setAccessibleName(_("…"))` 的调用，键是接收者源码，值是字面量。

    只认 `setAccessibleName` 且参数是 `_(...)` 里的**字符串常量** —— 那些
    动态拼出来的名字无法静态读出，也就不在这条判据的范围里。
    """
    tree = ast.parse(_CENTRAL.read_text(encoding="utf-8"))
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "setAccessibleName":
            continue
        if not node.args:
            continue
        arg = node.args[0]
        # setAccessibleName(_("文本"))
        if not (isinstance(arg, ast.Call) and len(arg.args) == 1):
            continue
        literal = arg.args[0]
        if not isinstance(literal, ast.Constant) or not isinstance(literal.value, str):
            continue
        found.setdefault(ast.unparse(func.value), literal.value)
    return found


def test_the_scan_is_not_vacuous() -> None:
    """★ 先确认扫描有收获 —— 否则下面的断言会变成空集对空集的假通过。"""
    names = _accessible_names()
    assert len(names) >= 3, f"只扫到 {len(names)} 处 setAccessibleName，扫描面不对"


@pytest.mark.parametrize(("container", "expected"), sorted(_CONTAINERS.items()))
def test_each_container_page_has_its_accessible_name(
    container: str, expected: str
) -> None:
    """★ 核心判据：三个 `central_area` 里临时拼的容器页必须有名字。

    它们没有自己的类，所以「在类里设」这条惯例覆盖不到。
    """
    names = _accessible_names()
    assert container in names, f"{container} 没有 setAccessibleName 调用"
    assert names[container] == expected, (
        f"{container} 的无障碍名是 {names[container]!r}，应为 {expected!r}"
    )


def test_the_three_nav_keys_map_to_those_containers() -> None:
    """★ 反过来确认：那三个容器确实是 workspace / monitor / results 的栈页。

    否则「给某个没人用的 QWidget 设了名字」也能让上面的断言通过。
    """
    tree = ast.parse(_CENTRAL.read_text(encoding="utf-8"))
    stack_pages: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        target = node.value
        if not (isinstance(target, ast.Call)
                and isinstance(target.func, ast.Attribute)
                and target.func.attr == "addWidget"
                and target.args):
            continue
        for tgt in node.targets:
            if not isinstance(tgt, ast.Subscript):
                continue
            if not isinstance(tgt.slice, ast.Attribute):
                continue
            if tgt.slice.attr not in {"WORKSPACE", "MONITOR", "RESULTS"}:
                continue
            stack_pages[tgt.slice.attr] = ast.unparse(target.args[0])
    assert stack_pages, "没找到 workspace/monitor/results 的栈页赋值"
    assert stack_pages.get("WORKSPACE") == "mw._workspace_widget"
    assert stack_pages.get("MONITOR") == "monitor_widget"
    assert stack_pages.get("RESULTS") == "results_widget"


def test_nav_bar_has_an_accessible_name() -> None:
    """导航栏自身也要有名（读屏器会先念容器）。"""
    names = _accessible_names()
    assert "mw._nav" in names, f"主导航栏没有 setAccessibleName：{sorted(names)}"
