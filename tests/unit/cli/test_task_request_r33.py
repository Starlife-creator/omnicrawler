"""`omnicrawler task` —— 中文需求解析在 CLI 上可达（走查 R3.3）。

## 背景（0.13.0 实测）

中文需求解析（`services/natural_language_task.compile_natural_language`）此前**只有 GUI 可达**：
`auto-analyze` 只接受 URL、根本没有"用户要什么"这一层，命令行与自动化场景拿不到。

★ 本文件最要紧的一条是 `test_task_command_has_a_handler`：命令进了解析器**不等于**能执行 ——
`.audit-tmp/w71` 的走查里，"声明了但未接线"正是反复出现的失败形态（confirmation 声明
「字段内容」可改却不承载、AI 的 config_patch 一度只校验不应用——后者见 R4.4）。解析器有、注册表没有，
用户看到的是 `未注册的命令`。
"""

from __future__ import annotations

import argparse
import json
import socket

import pytest

from omnicrawler.cli import _handlers, _main

_REQUEST = "抓取 https://books.toscrape.com 的全部 50 页书籍的标题、价格和库存状态，按价格从低到高排序，输出 CSV"


def _parse_json_out(raw: str) -> dict:
    """从命令输出里取出 JSON 块（启动日志可能混在同一流里）。"""
    start, end = raw.find("{"), raw.rfind("}")
    assert start >= 0 and end > start, f"输出里没有 JSON 对象：{raw[:200]!r}"
    return json.loads(raw[start : end + 1])


def test_task_command_is_registered_in_the_parser() -> None:
    parser = _main.build_parser()
    actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    subparsers = {name: p for a in actions for name, p in a.choices.items()}
    assert "task" in subparsers, "CLI 上没有 task 命令"
    contract = {option for item in subparsers["task"]._actions for option in item.option_strings}
    assert "--fallback-url" in contract
    positional = [a.dest for a in subparsers["task"]._actions if not a.option_strings]
    assert positional == ["request"]


def test_task_command_has_a_handler() -> None:
    """解析器里有的命令必须在分发注册表里也有 —— 否则用户拿到 `未注册的命令`。"""
    parser = _main.build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name in action.choices:
                if name in {"pdf"}:
                    continue
                assert _handlers.lookup(name) is not None, f"{name} 在解析器里没有处理函数"


def test_task_command_prints_reviewable_task_settings(capsys: pytest.CaptureFixture[str]) -> None:
    _main.main(["task", _REQUEST])
    payload = _parse_json_out(capsys.readouterr().out)
    assert payload["task"]["fields"] == ["标题", "价格", "库存状态"]
    assert payload["task"]["max_pages"] == 50
    assert payload["task"]["output_formats"] == ["csv"]
    assert payload["task"]["post_processing"] == ["排序"]
    # R5.1 起「排序」已有对等能力（`transform --sort`）⇒ **不得**再进 unsupported，
    # 但必须给出去处（只说"能做"而不给命令，等于把用户丢在半路）。
    assert payload["task"]["unsupported"] == []
    advice = "".join(payload["task"]["warnings"])
    assert "transform" in advice and "--sort" in advice
    assert payload["confirmation"]["后处理"] == ["排序"]
    assert payload["confirmation"]["字段内容"] == ["标题", "价格", "库存状态"]
    assert "next_step" in payload


def test_task_command_uses_the_fallback_url_when_the_request_has_none(
    capsys: pytest.CaptureFixture[str],
) -> None:
    _main.main(["task", "抓取 标题、价格，输出 CSV", "--fallback-url", "https://example.com/notices"])
    payload = _parse_json_out(capsys.readouterr().out)
    assert payload["task"]["url"] == "https://example.com/notices"


def test_task_command_rejects_an_empty_request(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        _main.main(["task", "   "])
    assert excinfo.value.code == 1
    assert "需求不能为空" in capsys.readouterr().err


def test_task_command_makes_no_network_call(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """契约：这一层**只做解析**。断网也必须给出同一份确认单。"""

    def _boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("解析需求时不应发起网络连接")

    monkeypatch.setattr(socket.socket, "connect", _boom)
    monkeypatch.setattr(socket.socket, "connect_ex", _boom)
    _main.main(["task", _REQUEST])
    payload = _parse_json_out(capsys.readouterr().out)
    assert payload["task"]["fields"] == ["标题", "价格", "库存状态"]
