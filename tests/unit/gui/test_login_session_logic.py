"""U3 会话页**纯逻辑**验收（无 Qt）——《优化方案》§11.1 U3。

这些才是会出错的地方：桥接给谁（目标站点集合）、剩余时间格式、不可用时的
引导文案、列表行的可解析性标注。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.core.config import AppConfig, load_config
from omnicrawler.fetching.login_session import LoginPhase
from omnicrawler.fetching.session_state import SessionSummary
from omnicrawler.gui.views.login_session_logic import (
    LoginHintGate,
    bridge_default,
    bridge_hosts,
    degradation_hint,
    detect_login_signal,
    effective_bridge_enabled,
    format_remaining,
    format_timestamp,
    notice_text,
    phase_label,
    session_row,
)


def _config(
    tmp_path: Path,
    *,
    seeds: str = "[https://example.org/, https://api.example.org/list]",
    session: str = "{persist_cookies: true, name: default}",
) -> AppConfig:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: u3, workspace: work}\n"
        f"source: {{kind: static_html, seeds: {seeds}}}\n"
        f"session: {session}\n",
        encoding="utf-8",
    )
    return load_config(config_path)


# ── 桥接开关与目标站点 ───────────────────────────────────


def test_bridge_defaults_to_on(tmp_path: Path) -> None:
    """§11.1 裁定 1：默认开（走 load_config 后的有效值）。"""
    assert bridge_default(_config(tmp_path, session="{persist_cookies: true}")) is True


def test_bridge_default_is_declared_in_config_defaults() -> None:
    """★ 「默认开」的**真源**在 `core/config.py` 的 DEFAULTS。

    ``bridge_default`` 里的兜底常量在正常路径上取不到（``load_config``/GUI 侧都会
    ``deep_merge(DEFAULTS, …)``），所以只断言"某个配置跑出来是 True"**抓不到**
    有人把默认值改成关 —— 必须直接钉住那个声明处。
    """
    from omnicrawler.core.config import DEFAULTS

    assert DEFAULTS["session"]["bridge_to_http"] is True


def test_bridge_default_can_be_overridden_by_config(tmp_path: Path) -> None:
    config = _config(tmp_path, session="{persist_cookies: true, bridge_to_http: false}")
    assert bridge_default(config) is False
    assert effective_bridge_enabled(config, None) is False
    assert effective_bridge_enabled(config, True) is True


def test_explicit_switch_wins_over_config(tmp_path: Path) -> None:
    """用户显式切换优先于配置默认；未切换（None）时跟随配置。"""
    config = _config(tmp_path, session="{persist_cookies: true, bridge_to_http: true}")
    assert effective_bridge_enabled(config, None) is True
    assert effective_bridge_enabled(config, False) is False


def test_bridge_hosts_uses_seeds_and_login_url(tmp_path: Path) -> None:
    config = _config(tmp_path)
    hosts = bridge_hosts(config, login_url="https://accounts.example.net/signin")
    assert hosts == ("example.org", "api.example.org", "accounts.example.net")


def test_bridge_hosts_dedupes_across_seeds_and_login_url(tmp_path: Path) -> None:
    """同一站点在种子与登录地址里各出现一次 ⇒ 只归还一次。

    顺带钉住：``urlsplit().hostname`` 会把主机名小写化（大小写不同也视为同一条）。
    """
    config = _config(
        tmp_path,
        seeds="[https://Example.org/, https://EXAMPLE.org/x, https://other.test/]",
    )
    assert bridge_hosts(config, login_url="https://example.ORG/") == ("example.org", "other.test")


def test_bridge_hosts_is_empty_without_any_source() -> None:
    """空集合必须能被如实产出（由会话桥显式报错，而不是悄悄"全给"）。

    直接构造 AppConfig：``load_config`` 会先拒绝"没有种子"的配置，
    而这里要验的正是**下游**面对空来源时的行为。
    """
    config = AppConfig(Path("task.yaml"), Path("."), {"source": {"seeds": []}}, Path("work"))
    assert bridge_hosts(config) == ()


# ── 显示格式 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0.0, "0:00"), (-5.0, "0:00"), (59.0, "0:59"), (60.0, "1:00"), (900.0, "15:00"), (899.9, "14:59")],
)
def test_format_remaining(seconds: float, expected: str) -> None:
    assert format_remaining(seconds) == expected


def test_format_timestamp_marks_missing_as_placeholder() -> None:
    assert format_timestamp(0.0) == "-"
    assert format_timestamp(1_700_000_000.0).startswith("2023-")


def test_phase_labels_cover_every_phase_uniquely() -> None:
    labels = [phase_label(phase) for phase in LoginPhase]
    assert all(labels)
    assert len(set(labels)) == len(labels)


# ── 降级引导 ─────────────────────────────────────────────


def test_no_hint_when_everything_available() -> None:
    assert degradation_hint(playwright_installed=True, persistence_enabled=True) == ""


def test_persistence_off_hint_is_actionable() -> None:
    hint = degradation_hint(playwright_installed=True, persistence_enabled=False)
    assert "persist_cookies" in hint


def test_missing_playwright_hint_is_actionable() -> None:
    hint = degradation_hint(playwright_installed=False, persistence_enabled=True)
    assert "Playwright" in hint
    assert "chromium" in hint


def test_persistence_hint_takes_priority_over_playwright() -> None:
    """两者都缺时先说"存不下来"—— 那种情况下装好浏览器也没用。"""
    hint = degradation_hint(playwright_installed=False, persistence_enabled=False)
    assert "persist_cookies" in hint


# ── 列表行 ───────────────────────────────────────────────


def _summary(**overrides: object) -> SessionSummary:
    values: dict[str, object] = {
        "name": "alice-abc123",
        "account": "alice",
        "path": Path("sessions/alice-abc123.playwright.json"),
        "modified_at": 1_700_000_000.0,
        "cookie_count": 7,
        "domains": (".example.org",),
        "readable": True,
    }
    values.update(overrides)
    return SessionSummary(**values)  # type: ignore[arg-type]


def test_session_row_shows_metadata_only() -> None:
    row = session_row(_summary())
    assert row[0] == "alice"
    assert row[1] == ".example.org"
    assert row[2] == "7"
    assert row[3].startswith("2023-")


def test_session_row_marks_unreadable_snapshot() -> None:
    """★ 解析不出来的快照照样列出并标注，不隐藏。"""
    row = session_row(_summary(readable=False, cookie_count=0, domains=()))
    assert row[2] == "-"
    assert "无法解析" in row[1]


def test_session_row_without_domains_uses_placeholder() -> None:
    assert session_row(_summary(domains=()))[1] == "-"


# ── 一次性提醒正文 ───────────────────────────────────────


def test_notice_text_states_all_four_facts(tmp_path: Path) -> None:
    """裁定 1 要求讲清：cookie 属凭据 / 落盘位置 / 保护方式 / 关闭入口。"""
    text = notice_text(sessions_dir=str(tmp_path / "sessions"), bridge_enabled=True)
    assert str(tmp_path / "sessions") in text
    assert "0600" in text
    assert "明文" in text
    assert "关闭" in text
    assert "同步" in text


def test_notice_text_reflects_bridge_state() -> None:
    on = notice_text(sessions_dir="s", bridge_enabled=True)
    off = notice_text(sessions_dir="s", bridge_enabled=False)
    assert on != off
    assert "不会同步" in off


# ── U4-代码：「需要登录」信号 ─────────────────────────────


@pytest.mark.parametrize(
    "message",
    [
        "登录失败: HTTP 401",
        "GET https://example.org/list -> HTTP/1.1 401 Unauthorized",
        "fetch failed: status_code=401",
        "REQUEST FAILED code: 401",
        "401 Unauthorized",
    ],
)
def test_unauthorized_messages_are_detected(message: str) -> None:
    signal = detect_login_signal(message)
    assert signal is not None
    assert signal.status == 401
    assert signal.reason


@pytest.mark.parametrize(
    "message",
    [
        "记录 401 已保存",
        "page=4 offset=401",
        "HTTP/1.1 403 Forbidden",  # 403 是权限问题，去登录页没有意义
        "HTTP/1.1 200 OK",
        "connection reset by peer",
        "",
    ],
)
def test_non_login_messages_are_not_flagged(message: str) -> None:
    """★ 判据刻意窄：误报会把用户推去登录一个本来不需要登录的站点。"""
    assert detect_login_signal(message) is None


def test_redirect_to_login_page_is_detected_with_url() -> None:
    signal = detect_login_signal("GET /list -> 302 Found Location: https://example.org/login?next=/list")
    assert signal is not None
    assert signal.status is None
    assert signal.login_url == "https://example.org/login?next=/list"


def test_redirect_without_login_url_is_not_flagged() -> None:
    """302 落在**非**登录页 ⇒ 不提示（否则每次跳转都会劝人去登录）。

    ★ 这里必须用**被纳入**的状态码（302）：用 301 的话根本没走到该分支，
    这条"负例"就成了假判据（反向断言实测暴露过）。
    """
    assert detect_login_signal("302 -> https://example.org/moved") is None


def test_permanent_redirect_is_not_treated_as_login_redirect() -> None:
    """301（永久跳转）不在纳入范围：它通常只是站点改址，不是登录墙。"""
    assert detect_login_signal("301 -> https://example.org/login") is None


def test_unauthorized_signal_carries_login_url_when_present() -> None:
    signal = detect_login_signal("HTTP 401 -> https://example.org/signin")
    assert signal is not None
    assert signal.login_url == "https://example.org/signin"


def test_hint_gate_announces_only_once_per_run() -> None:
    gate = LoginHintGate()
    assert gate.announced is False

    first = gate.should_announce("HTTP 401 Unauthorized")
    second = gate.should_announce("HTTP 401 Unauthorized")

    assert first is not None and second is None
    assert gate.announced is True


def test_hint_gate_reset_re_arms() -> None:
    gate = LoginHintGate()
    assert gate.should_announce("HTTP 401") is not None
    gate.reset()
    assert gate.announced is False
    assert gate.should_announce("HTTP 401") is not None


def test_hint_gate_ignores_unrelated_lines() -> None:
    gate = LoginHintGate()
    assert gate.should_announce("已抓取 10 条记录") is None
    assert gate.announced is False
