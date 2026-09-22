"""`tools/check_agent_surface.py` 的自测 —— 重点是**每一类缺口都会被抓到**。

契约类门禁最容易出现的失效是「看起来在跑、其实恒真」：命令数为 0 时所有断言都过，
或者只检查了其中一侧（解析器有 ⇒ 注册表有没有？）。这里逐类反向验证。
"""

from __future__ import annotations

import json

import pytest

from tools.check_agent_surface import build_surface, main, validate_surface


def _command(name: str, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": name,
        "in_parser": True,
        "has_handler": True,
        "handler": "omnicrawler.cli._handlers._run_x",
        "subcommands": [],
        "options": [],
        "stdout": "json",
        "stdout_source": "verified",
        "stdout_note": None,
        "observed_exit_codes": [0],
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------------
# 基线：真实 CLI 的契约必须完整且自洽
# --------------------------------------------------------------------------


def test_real_surface_passes_validation() -> None:
    surface = build_surface()
    assert surface["commands"], "契约不能为空：命令数为 0 时所有断言恒真"
    assert validate_surface(surface) == []


def test_every_command_has_a_resolved_stdout_form() -> None:
    """形态要么可验证（源码里确有 `_json(...)`），要么显式声明 —— 不接受「猜它是 JSON」。"""
    unknown = [c["name"] for c in build_surface()["commands"] if c["stdout"] is None]
    assert unknown == [], f"这些命令的 stdout 形态既不可验证、也未声明：{unknown}"


def test_parser_and_registry_agree_in_both_directions() -> None:
    """双向：解析器里有 ⇒ 注册表里必须有处理函数；反之亦然。"""
    for command in build_surface()["commands"]:
        assert command["in_parser"] and command["has_handler"], command["name"]


def test_convert_is_declared_or_verified_as_json() -> None:
    """回归：P2-2 修的就是 `convert` 的 stdout 纯度，契约必须能把它表达出来。"""
    convert = next(c for c in build_surface()["commands"] if c["name"] == "convert")
    assert convert["stdout"] == "json"


# --------------------------------------------------------------------------
# 每类缺陷都必须被抓到（反向断言）
# --------------------------------------------------------------------------


def test_empty_surface_is_rejected() -> None:
    issues = validate_surface({"commands": []})
    assert issues and "命令数为 0" in issues[0]


def test_command_without_handler_is_caught() -> None:
    """解析器里有 ≠ 能执行。"""
    issues = validate_surface({"commands": [_command("ghost", has_handler=False)]})
    assert any("注册表里没有处理函数" in item for item in issues)


def test_handler_without_parser_entry_is_caught() -> None:
    """反向：注册表里有处理函数，但用户敲不出这个命令。"""
    issues = validate_surface({"commands": [_command("orphan", in_parser=False)]})
    assert any("解析器里没有该命令" in item for item in issues)


def test_unresolvable_stdout_is_caught() -> None:
    """既不验证也不声明 ⇒ 必须点名（不许默认 JSON）。"""
    issues = validate_surface({"commands": [_command("mystery", stdout=None, stdout_source="unknown")]})
    assert any("mystery" in item and "stdout" in item for item in issues)


def test_main_json_output_is_parseable(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == "agent-surface/1"
    assert payload["commands"]


def test_main_check_returns_zero_for_real_cli(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--check"]) == 0
    assert "OK" in capsys.readouterr().out
