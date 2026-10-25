"""A-P3-8：CLI 错误出口脱敏管道 + 调试模式才输出原始异常回归测试。

- 非调试模式：异常消息经 redact_value 管道，URL 内嵌凭据不得出现在 stderr；
- 调试模式（--log-level DEBUG）：保留原始异常文本与堆栈，供排障；
- 退出码保持 1，错误提示分支不受影响。
"""
from __future__ import annotations

import pytest

from omnicrawler.cli import _handlers, _main

_SECRET_URL = "https://dbuser:hunter2@db.example.com:5432/path"


def test_format_cli_error_redacts_credentials_by_default() -> None:
    exc = RuntimeError(f"连接失败: {_SECRET_URL}")
    message = _main._format_cli_error(exc)
    assert "hunter2" not in message
    assert "<redacted>" in message
    assert "RuntimeError" in message


def test_format_cli_error_keeps_raw_in_debug() -> None:
    exc = RuntimeError(f"连接失败: {_SECRET_URL}")
    message = _main._format_cli_error(exc, debug=True)
    assert "hunter2" in message
    assert "RuntimeError" in message


def test_main_error_stderr_is_redacted(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_args: object) -> None:
        raise RuntimeError(f"连接失败: {_SECRET_URL}")

    monkeypatch.setattr(_handlers, "lookup", lambda _name: _boom)
    with pytest.raises(SystemExit) as exc_info:
        _main.main(["templates", "list"])
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "hunter2" not in captured.err
    assert "<redacted>" in captured.err


def test_main_debug_mode_keeps_raw_exception(capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_args: object) -> None:
        raise RuntimeError(f"连接失败: {_SECRET_URL}")

    monkeypatch.setattr(_handlers, "lookup", lambda _name: _boom)
    with pytest.raises(SystemExit) as exc_info:
        _main.main(["--log-level", "DEBUG", "templates", "list"])
    assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "hunter2" in captured.err
    # 调试模式同时打印堆栈
    assert "Traceback" in captured.err
