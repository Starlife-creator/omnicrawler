"""插件自检必须覆盖**全部**实际使用的第三方依赖。

## 这条守卫防的是什么

2026-11-23 实测：插件实际 import 24 个第三方包，而 `selfcheck()` 的
`optional` 清单只列了 6 个——**18 个未覆盖**。

后果：用户在缺 `pyarrow` / `duckdb` / `python-pptx` 的机器上装插件，
自检报"一切正常"，直到用某个格式时才 `ImportError`。

这与市场门 3 的缺口是同一个根因的两面：

* 门 3 只看 `plugin.py` 的导入图，不看子模块；
* `selfcheck` 的手写清单容易与真实导入漂移。

## 判据

1. 扫描插件**全部** `.py` 的第三方导入（排除标准库、宿主、SDK、本地模块）；
2. 断言每一项都出现在 `selfcheck()` 的 required + optional 清单里；
3. 反向：清单里的每一项也应当是真实用到的（防"列了但没用"）。

`omnicrawler_docs` 自身不算第三方（它是包内模块）。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

_PLUGIN = (
    Path(__file__).resolve().parents[4]
    / "OmniCrawler-market"
    / "submissions"
    / "plugins"
    / "4c3014804d85de2568f85e62a3048429"
    / "document-workbench"
)

#: 不算第三方：宿主接口（契约 1 允许导入）、SDK、包自身。
_NON_THIRD_PARTY = {"omnicrawler", "omnicrawler_sdk", "omnicrawler_docs"}


def _local_module_names() -> set[str]:
    """插件内所有模块的 stem（用于排除同包本地导入）。"""
    return {p.stem for p in _PLUGIN.rglob("*.py") if "__pycache__" not in p.parts}


def _third_party_imports() -> set[str]:
    """插件实际使用的第三方顶层模块名。"""
    local = _local_module_names()
    stdlib = set(getattr(sys, "stdlib_module_names", set()))
    found: set[str] = set()
    for path in _PLUGIN.rglob("*.py"):
        # 排除插件自带的测试：它们用 pytest 等测试期依赖，
        # 不是运行时依赖（跑了 `pip install document-workbench` 不该被要求装 pytest）。
        if "__pycache__" in path.parts or "tests" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module.split(".")[0]]
            for name in modules:
                if name in stdlib or name in _NON_THIRD_PARTY or name in local:
                    continue
                found.add(name)
    return found


def _selfcheck_declared() -> set[str]:
    """`selfcheck()` 声明的依赖（required + optional 的键）。"""
    source = (_PLUGIN / "plugin.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    declared: set[str] = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "required" in names:
            if isinstance(node.value, ast.List):
                for element in node.value.elts:
                    if isinstance(element, ast.Constant) and isinstance(element.value, str):
                        declared.add(element.value)
        elif "optional" in names:
            if isinstance(node.value, ast.Dict):
                for key in node.value.keys:
                    if isinstance(key, ast.Constant) and isinstance(key.value, str):
                        declared.add(key.value)
    return declared


@pytest.mark.skipif(not _PLUGIN.is_dir(), reason="需要 market 仓插件目录")
def test_selfcheck_covers_every_third_party_import() -> None:
    """实际 import 的每个第三方包都必须出现在 selfcheck 清单里。"""
    actual = _third_party_imports()
    declared = _selfcheck_declared()

    assert actual, "没扫到任何第三方导入——AST 解析或路径推算可能失效"
    assert declared, "没解析出 selfcheck 的依赖清单——AST 规则可能过期"

    missing = sorted(actual - declared)
    assert not missing, (
        f"selfcheck 未覆盖 {len(missing)} 个实际使用的第三方包：{missing}\n"
        "缺依赖时自检会误报\u201c一切正常\u201d，用户到用时才 ImportError。"
    )


@pytest.mark.skipif(not _PLUGIN.is_dir(), reason="需要 market 仓插件目录")
def test_selfcheck_declares_nothing_unused() -> None:
    """反向：selfcheck 列出的包必须真的被用到（防清单腐化）。"""
    actual = _third_party_imports()
    declared = _selfcheck_declared()

    # `required` 里是包内模块路径（如 omnicrawler_docs.pdfx.config），
    # 与第三方顶层名不是同一维度，只比较"看起来像第三方"的键。
    extra = sorted(
        name for name in declared - actual
        if "." not in name
        and name not in _NON_THIRD_PARTY
        and name not in _local_module_names()
    )
    assert not extra, (
        f"selfcheck 声明了 {len(extra)} 个未被使用的包：{extra}\n"
        "清单与实际导入脱节会让用户被要求装不需要的东西。"
    )
