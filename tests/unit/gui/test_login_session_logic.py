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
    bridge_default,
    bridge_hosts,
    degradation_hint,
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
