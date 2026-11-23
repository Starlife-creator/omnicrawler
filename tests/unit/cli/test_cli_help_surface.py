"""CLI `--help` 的 agent 可用性守卫。

## 为什么这个测试存在（2026-11-23 调研后）

调研 clig.dev / GitHub CLI / tooli 后确认三件事，本项目此前都缺：

1. **命令必须分组** —— 55 个子命令平铺一屏，人和 agent 都找不到入口；
2. **示例要前置** —— clig.dev 明确 "lead with examples"；
3. **退出码要有契约** —— agent 靠它决定「重试 / 改输入 / 放弃」。

这三样现在都在 `--help` 的 epilog 里。本测试固定它们**不退化**。

## 判据（确定性）

* 每个已注册命令**必须**出现在某个用户分组里（否则「其它」段会漏掉它；
  漏掉的命令等于在 help 里不存在）；
* 分组里不得出现**不存在的命令**（那是拼写错误，会误导）；
* 退出码 0/1/2 的语义必须写在 help 里；
* 至少有一个示例。

## 边界

不验文案质量，只验**结构完整性**。文案靠 review。
"""
from __future__ import annotations

import argparse

import pytest

from omnicrawler.cli import _main


@pytest.fixture(scope="module")
def parser() -> argparse.ArgumentParser:
    return _main.build_parser()


def test_every_command_appears_in_a_user_group(parser) -> None:
    """**核心守卫**：任何已注册命令都必须在 help 分组里露面。

    漏掉的命令等于「在 help 里不存在」——agent 永远发现不了它。
    """
    registered = set(_main._ALL_COMMANDS)
    assert registered, "没有注册任何命令"

    grouped: set[str] = set()
    for _title, names in _main.USER_COMMAND_GROUPS:
        grouped.update(names)

    missing = sorted(registered - grouped)
    assert not missing, (
        f"这些命令没出现在任何 help 分组里，agent 无法发现：{missing}"
    )


def test_groups_do_not_reference_unknown_commands() -> None:
    """分组里不得有不存在的命令名（拼写错误会误导使用者）。"""
    registered = set(_main._ALL_COMMANDS)
    ghosts: list[str] = []
    for title, names in _main.USER_COMMAND_GROUPS:
        for name in names:
            if name not in registered:
                ghosts.append(f"{title} -> {name}")
    assert not ghosts, f"分组引用了不存在的命令：{ghosts}"


def test_no_command_is_listed_twice() -> None:
    """同一命令不得出现在两个分组（会让人以为有两个入口）。"""
    seen: dict[str, str] = {}
    dupes: list[str] = []
    for title, names in _main.USER_COMMAND_GROUPS:
        for name in names:
            if name in seen:
                dupes.append(f"{name}（{seen[name]} 与 {title}）")
            else:
                seen[name] = title
    assert not dupes, f"命令被重复分组：{dupes}"


def test_examples_are_present_and_non_empty() -> None:
    """至少要有示例，且每条都非空（clig.dev: lead with examples）。"""
    assert _main.CLI_EXAMPLES, "没有任何示例"
    for label, command in _main.CLI_EXAMPLES:
        assert label.strip(), "示例缺少说明文字"
        assert command.strip().startswith("omnicrawler"), (
            f"示例不是可复制的完整命令：{command!r}"
        )


def test_exit_codes_are_documented(parser) -> None:
    """退出码 0/1/2 的语义必须出现在 help 里。

    agent 靠它分支：0 成功 / 1 可重试 / 2 先改输入。
    此前这套约定只存在于 `check_agent_surface.py --json` 的导出里，
    用户和 agent 在 `--help` 中看不到。
    """
    codes = {code for code, _ in _main.EXIT_CODE_HELP}
    assert {"0", "1", "2"} <= codes, f"退出码契约不全：{codes}"

    help_text = parser.format_help()
    assert "退出码" in help_text, "--help 里没有退出码说明"
    for code in ("0", "1", "2"):
        assert code in help_text


def test_help_renders_groups_examples_and_exit_codes(parser) -> None:
    """三段内容必须真的渲染进 `--help`（常量存在不等于显示出来）。"""
    text = parser.format_help()
    assert "常用示例" in text, "--help 里没有示例段"
    assert "命令分组" in text, "--help 里没有分组段"
    assert "退出码" in text, "--help 里没有退出码段"
    # 至少一个分组标题要出现
    assert any(title in text for title, _ in _main.USER_COMMAND_GROUPS), (
        "分组标题一个都没渲染出来"
    )
