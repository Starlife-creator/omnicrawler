"""CLI 注册表契约测试（FINAL 长期债 #4）。

覆盖：
    - build_parser 的子命令集合 ↔ _handlers._registry 双向一致（防漂移）
    - 每个命令的 --help 可用且退出码为 0
    - 每个 handler 可调用

此前 47 个命令的一致性仅靠人工核对（审查报告 T-1 盲区），本测试自动抓取。
"""

from __future__ import annotations

import argparse
import contextlib
import io

import pytest

from omnicrawler.cli._handlers import _registry
from omnicrawler.cli._main import build_parser


def _subcommand_choices(parser: argparse.ArgumentParser) -> set[str]:
    """从顶层 parser 提取子命令 choices（经由唯一的 _SubParsersAction）。"""
    actions = [
        action
        for action in parser._actions  # noqa: SLF001 - 测试内省 argparse 内部结构
        if isinstance(action, argparse._SubParsersAction)
    ]
    assert len(actions) == 1, "顶层应恰好存在一个子命令挂载点"
    return set(actions[0].choices)


def test_parser_subcommands_match_registry_exactly() -> None:
    """parser 定义的命令与注册表处理函数必须双向一一对应。"""
    parser = build_parser()
    choices = _subcommand_choices(parser)
    registered = set(_registry)

    missing_handlers = choices - registered
    missing_parsers = registered - choices
    assert not missing_handlers, f"有 parser 无 handler: {sorted(missing_handlers)}"
    assert not missing_parsers, f"有 handler 无 parser: {sorted(missing_parsers)}"


def test_every_registered_handler_is_callable() -> None:
    for name, handler in _registry.items():
        assert callable(handler), f"命令 {name} 的 handler 不可调用"


@pytest.mark.parametrize("command", sorted(_registry))
def test_command_help_runs_clean(command: str) -> None:
    """每个命令的 --help 必须可构建且以退出码 0 结束（参数定义无损坏）。"""
    parser = build_parser()
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), pytest.raises(SystemExit) as excinfo:
        parser.parse_args([command, "--help"])
    assert excinfo.value.code == 0, f"命令 {command} --help 退出码异常"
    assert buffer.getvalue().strip(), f"命令 {command} --help 输出为空"
