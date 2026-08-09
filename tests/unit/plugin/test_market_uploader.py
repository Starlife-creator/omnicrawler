"""Tests for the market uploader (gh CLI wrapper)."""

from __future__ import annotations

import pytest

from omnicrawl.plugins import market_uploader
from omnicrawl.plugins.market_uploader import (
    UploadError,
    create_market_pr,
    ensure_gh,
    pr_body,
)


def test_pr_body_mentions_maintainer_signature() -> None:
    body = pr_body("插件", "demo", "alice")
    assert "demo" in body
    assert "sign_plugin.py" in body


def test_create_market_pr_rejects_unsafe_paths() -> None:
    with pytest.raises(UploadError, match="非法文件路径"):
        create_market_pr(files={"../evil.txt": b"x"}, title="t", body="b")
    with pytest.raises(UploadError, match="非法文件路径"):
        create_market_pr(files={"/abs.txt": b"x"}, title="t", body="b")
    with pytest.raises(UploadError, match="为空"):
        create_market_pr(files={}, title="t", body="b")


def test_ensure_gh_reports_missing_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    def _missing(*args, **kwargs):
        raise UploadError("找不到可执行文件 gh")

    monkeypatch.setattr(market_uploader, "_run", _missing)
    with pytest.raises(UploadError, match="gh"):
        ensure_gh()


def test_ensure_gh_reports_not_logged_in(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def _fake(command: list[str], **kwargs):
        calls.append(command)
        if command[0] == "gh" and command[1] == "--version":
            return type("R", (), {"returncode": 0})()
        if command[0] == "gh" and command[1] == "auth":
            return type("R", (), {"returncode": 0, "stdout": "session expired"})()
        raise AssertionError(f"unexpected command: {command}")

    monkeypatch.setattr(market_uploader, "_run", _fake)
    with pytest.raises(UploadError, match="登录"):
        ensure_gh()
