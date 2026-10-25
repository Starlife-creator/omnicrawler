"""A-P3-2：ScopePolicy 用户正则预编译缓存 + 配置期非法正则拒绝回归测试。

- 策略对象初始化时把 deny/allow/write-guard 正则一次性编译缓存；
- 多次 ``allowed`` 判定不再重复 ``re.compile``；
- 非法正则在 :func:`validate_config` 配置校验期即被拒绝。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from omnicrawler.core.config import DEFAULTS, AppConfig, validate_config
from omnicrawler.security.policy import ScopePolicy


class _AllowAllNetwork:
    def allowed(self, url: str) -> tuple[bool, str]:
        return True, ""


def _policy(tmp_path: Path, crawl: dict) -> ScopePolicy:
    raw = {
        "project": {"name": "regex-cache", "workspace": "work"},
        "source": {"kind": "static_html", "seeds": ["https://example.com/start"]},
        "crawl": {**DEFAULTS["crawl"], **crawl},
        "http": {**DEFAULTS["http"], "user_agent": "test@example.com"},
        "egress": dict(DEFAULTS["egress"]),
    }
    policy = ScopePolicy(AppConfig(tmp_path / "task.yaml", tmp_path, raw, tmp_path / "work"))
    policy._network = _AllowAllNetwork()  # type: ignore[assignment]
    return policy


def test_patterns_compiled_once_at_initialization(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    policy = _policy(
        tmp_path,
        {
            "deny_patterns": [r"/blocked/"],
            "allow_patterns": [r"example\.com"],
            "allow_write_patterns": [r"cart"],
        },
    )
    assert len(policy._deny_patterns) == 1
    assert len(policy._allow_patterns) == 1
    assert len(policy._write_guard_patterns) == 1
    assert all(isinstance(pattern, re.Pattern) for pattern in policy._deny_patterns)

    # 判定阶段不得再触发编译。
    compile_calls: list[str] = []
    real_compile = re.compile

    def _spy(pattern: str | re.Pattern[str], *flags: int) -> re.Pattern[str]:
        compile_calls.append(str(pattern))
        return real_compile(pattern, *flags)

    monkeypatch.setattr(re, "compile", _spy)
    policy.allowed("https://example.com/blocked/x", "https://example.com/")
    policy.allowed("https://example.com/ok", "https://example.com/")
    assert compile_calls == []


def test_deny_and_allow_cache_behavior_unchanged(tmp_path: Path) -> None:
    policy = _policy(
        tmp_path,
        {"deny_patterns": [r"/blocked/"], "allow_patterns": [r"example\.com"]},
    )
    blocked, reason = policy.allowed("https://example.com/blocked/x", "https://example.com/")
    assert not blocked and reason == "命中deny_patterns"
    allowed, _ = policy.allowed("https://example.com/ok", "https://example.com/")
    assert allowed


def _config_with_bad_pattern(tmp_path: Path, pattern: str) -> AppConfig:
    raw = {
        "project": {"name": "bad-regex", "workspace": "work"},
        "source": {"kind": "static_html", "seeds": ["https://example.com/start"]},
        "crawl": {**DEFAULTS["crawl"], "deny_patterns": [pattern]},
        "http": dict(DEFAULTS["http"]),
        "egress": dict(DEFAULTS["egress"]),
    }
    return AppConfig(tmp_path / "task.yaml", tmp_path, raw, tmp_path / "work")


def test_invalid_deny_pattern_rejected_at_config_validation(tmp_path: Path) -> None:
    errors, _warnings = validate_config(_config_with_bad_pattern(tmp_path, r"([unclosed"))
    assert any("deny_patterns" in message and "正则非法" in message for message in errors)


def test_invalid_allow_pattern_rejected_at_config_validation(tmp_path: Path) -> None:
    raw = {
        "project": {"name": "bad-regex", "workspace": "work"},
        "source": {"kind": "static_html", "seeds": ["https://example.com/start"]},
        "crawl": {**DEFAULTS["crawl"], "allow_patterns": [r"a(b|"]},
        "http": dict(DEFAULTS["http"]),
        "egress": dict(DEFAULTS["egress"]),
    }
    errors, _warnings = validate_config(AppConfig(tmp_path / "task.yaml", tmp_path, raw, tmp_path / "work"))
    assert any("allow_patterns" in message and "正则非法" in message for message in errors)
