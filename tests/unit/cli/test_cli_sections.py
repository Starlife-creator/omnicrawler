"""CLI `--help` 的 section 顺序必须**显式且完整**（W6.7-②）。

## 背景

section 顺序此前是 `build_parser()` 里的一行元组字面量 —— 新加 section 时它自然落在**末尾**
（`pdf` 就是这么排到最后去的），"顺序"于是成了注册动作的副产品而不是一个决定。
现在有 `SECTION_ORDER` 常量（规则：用户旅程），并由本文件守卫：

1. `_parsers/` 下**每个**模块都必须在 `SECTION_ORDER` 里（新加 section 忘登记 ⇒ 判红）；
2. `SECTION_ORDER` 里每个名字都必须真的是 `_parsers` 下的模块（写错名 ⇒ 判红）；
3. `build_parser()` **只按表注册**（源码里不得再出现"元组字面量顺序"那种写法）。
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PARSERS_DIR = _REPO_ROOT / "src" / "omnicrawler" / "cli" / "_parsers"
_MAIN = _REPO_ROOT / "src" / "omnicrawler" / "cli" / "_main.py"


def _section_modules() -> list[str]:
    return sorted(
        path.stem
        for path in _PARSERS_DIR.glob("*.py")
        if path.stem != "__init__" and "__pycache__" not in path.parts
    )


def test_probe_is_not_vacuous() -> None:
    """先确认扫到了 section 模块，否则下面的断言会是空集对空集的假通过。"""
    modules = _section_modules()
    assert len(modules) >= 5, f"只扫到 {modules}，路径推断可能写错了"


def test_every_parser_module_is_registered() -> None:
    """`_parsers/` 下的每个模块都必须在 `SECTION_ORDER` 里 —— 新加 section 不许"悄悄出现"。"""
    from omnicrawler.cli._main import SECTION_ORDER

    missing = [name for name in _section_modules() if name not in SECTION_ORDER]
    assert not missing, (
        f"这些 section 模块没登记进 SECTION_ORDER：{missing}。"
        f"请在 `cli/_main.py` 里显式决定它排在哪（顺序即用户旅程），不要依赖注册顺序。"
    )


def test_every_registered_section_exists_as_a_module() -> None:
    """反向：表里每个名字都必须能 import —— 否则帮助会在运行时炸。"""
    import importlib

    from omnicrawler.cli._main import SECTION_ORDER

    for name in SECTION_ORDER:
        module = importlib.import_module(f"omnicrawler.cli._parsers.{name}")
        assert hasattr(module, "configure"), f"{name} 模块缺少 configure(sub) 入口"


def test_build_parser_registers_only_from_the_declared_order() -> None:
    """源码守卫：`build_parser()` 里不得再出现"元组字面量顺序"的写法。"""
    source = _MAIN.read_text(encoding="utf-8")
    assert "for name in SECTION_ORDER:" in source, "build_parser 未按 SECTION_ORDER 注册"
    assert "for section in (task, templates" not in source, (
        "又出现了写死的 section 元组字面量 —— 顺序必须只有 SECTION_ORDER 一个来源"
    )


def _commands_per_section() -> dict[str, list[str]]:
    """对每个 section 模块**单独**跑一次 configure，记下它注册了哪些子命令。

    （section 名本身不一定出现在帮助里 —— 帮助列的是**子命令**，例如 `task` 段注册的是
    `run` / `wizard` 等。所以要按"这段注册了什么"来断言，而不是按段名出现。）
    """
    import argparse
    import importlib

    from omnicrawler.cli._main import SECTION_ORDER

    mapping: dict[str, list[str]] = {}
    for name in SECTION_ORDER:
        parent = argparse.ArgumentParser()
        sub = parent.add_subparsers(dest="command")
        module = importlib.import_module(f"omnicrawler.cli._parsers.{name}")
        module.configure(sub)
        mapping[name] = list(sub.choices)
    return mapping


def test_every_section_contributes_commands() -> None:
    """每个 section 都必须真的注册出命令（空段 ⇒ 表里多余或 configure 失效）。"""
    mapping = _commands_per_section()
    empty = [name for name, commands in mapping.items() if not commands]
    assert not empty, f"这些 section 没注册任何子命令：{empty}"


def test_help_lists_every_section_command() -> None:
    """行为冒烟：`--help` 必须列出每个 section 注册的子命令。"""
    from omnicrawler.cli._main import build_parser

    help_text = build_parser().format_help()
    assert help_text.strip(), "帮助文本为空"
    missing: list[str] = []
    for name, commands in _commands_per_section().items():
        missing += [f"{name}:{command}" for command in commands if command not in help_text]
    assert not missing, f"帮助里找不到这些子命令：{missing}"


def test_help_orders_sections_by_the_declared_order() -> None:
    """`pdf` 曾经"自然排在最末" —— 现在顺序必须跟 SECTION_ORDER 一致。

    判定：取每段**第一个**子命令在帮助里的首次出现位置，要求**递增**。
    """
    from omnicrawler.cli._main import SECTION_ORDER, build_parser

    help_text = build_parser().format_help()
    mapping = _commands_per_section()
    positions: list[tuple[str, int]] = []
    for name in SECTION_ORDER:
        commands = mapping[name]
        if not commands:
            continue
        first = min((help_text.find(command) for command in commands if command in help_text), default=-1)
        assert first >= 0, f"{name} 的子命令一个都没出现在帮助里"
        positions.append((name, first))

    ordered = [name for name, _ in sorted(positions, key=lambda item: item[1])]
    assert ordered == [name for name, _ in positions], (
        f"帮助里的段顺序与 SECTION_ORDER 不一致：实际 {ordered}，期望 {[n for n, _ in positions]}"
    )
