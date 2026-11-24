"""--json 在**库调用**下也必须产出 envelope。

为什么要有这个文件：`_ArgumentParser.error()` 原先用 `sys.argv` 判断要不要出 JSON。
CLI 进程里 `sys.argv` 当然带着 `--json`，所以命令行路径一直是对的；
但库调用（`build_parser().parse_args(["...", "--json"])`）时 `sys.argv` 里没有它 ——
于是「`--json` 时 stdout 是合法 JSON」这个承诺在那种用法下**静默失效**。

实测（2026-11-24）：库调用下 stdout 是空串，只剩 argparse 打到 stderr 的 usage。

本文件钉住三条：

* 库调用 + `--json` ⇒ stdout 有合法 envelope；
* 库调用不带 `--json` ⇒ 不去污染 stdout；
* 子命令层的错误（不是顶层）同样产出 envelope —— 子解析器是独立对象，
  需要有机制把 argv 传下去。
"""

from __future__ import annotations

import json
import sys

import pytest

from omnicrawler.cli._main import build_parser


def _parse(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], argv: list[str]):
    """在“库调用”语义下解析：sys.argv 不含 argv 里的任何 `--json`。"""
    monkeypatch.setattr(sys, "argv", ["omnicrawler"])
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(argv)
    return capsys.readouterr()


def _last_json(stdout: str):
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        return None
    try:
        return json.loads(lines[-1])
    except json.JSONDecodeError:
        return None


def test_library_call_with_json_gets_an_envelope(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """库调用 + --json ⇒ stdout 必须有 envelope（此前为空）。"""
    captured = _parse(monkeypatch, capsys, ["components", "install", "nope", "--json"])
    payload = _last_json(captured.out)
    assert payload is not None, f"stdout 没有 JSON：{captured.out!r}"
    assert payload["ok"] is False
    assert payload["exit_code"] == 2
    assert payload["error"]["type"] == "UsageError"
    assert payload["error"]["is_retryable"] is False


def test_library_call_without_json_does_not_pollute_stdout(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """没有 --json 时不得往 stdout 写 envelope —— 否则人读输出被 JSON 混进来。"""
    captured = _parse(monkeypatch, capsys, ["components", "install", "nope"])
    assert captured.out.strip() == "", captured.out


def test_subparser_level_error_also_gets_an_envelope(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """子命令层的错误同样要出 envelope（子解析器是独立对象）。"""
    captured = _parse(monkeypatch, capsys, ["export", "--json"])
    payload = _last_json(captured.out)
    assert payload is not None, f"子命令层没有 JSON：{captured.out!r}"
    assert payload["error"]["type"] == "UsageError"


def test_json_flag_position_does_not_matter(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """`--json` 前中后位置都必须生效（clig.dev 要求）。"""
    for argv in (["export", "--json"], ["--json", "export"]):
        captured = _parse(monkeypatch, capsys, argv)
        payload = _last_json(captured.out)
        assert payload is not None, f"{argv} 没有产出 envelope"


def test_seen_argv_is_recorded_on_the_parser(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_seen_argv` 必须反映**本次解析**的 argv，而不是进程 argv。"""
    monkeypatch.setattr(sys, "argv", ["omnicrawler"])
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["export", "--json"])
    assert getattr(parser, "_seen_argv", None) == ["export", "--json"]


def test_build_payload_accepts_an_explicit_retryable_override() -> None:
    """通用异常类型上，调用方要能覆盖 `is_retryable`。

    类型推断只能回答「这类异常一般可否重试」：`RuntimeError` 默认可重试，
    而「插件未安装」这种具体原因**原样重试永远不会成功**。
    让调用方显式指定，比让 agent 照着类型名猜可靠。
    """
    from omnicrawler.cli._json_envelope import build_payload

    exc = RuntimeError("插件未安装")
    default = build_payload("pdf", ok=False, output="", exit_code=1, exc=exc)
    assert default["error"]["is_retryable"] is True, "RuntimeError 的默认推断是可重试"

    pinned = build_payload(
        "pdf", ok=False, output="", exit_code=1, exc=exc, is_retryable=False
    )
    assert pinned["error"]["is_retryable"] is False, "显式指定必须生效"
