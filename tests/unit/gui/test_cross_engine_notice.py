"""跨内核复用登录态时必须在界面上显式提示。

## 为什么

登录态与内核指纹绑定。用 Chromium 登录拿到的 cookie 到 Firefox 下复用，
UA 与 TLS 指纹不一致，站点可能直接判定会话异常——而用户看到的只是
"我明明登录过，怎么又要登录"，现场没有线索。

优化方案 §3.2(b)：

> 跨内核复用时在界面给出显式提示而非静默混用。

## 判据

1. 快照内核 == 当前内核 ⇒ **无提示**（正常路径不该打扰用户）；
2. 两者不同 ⇒ 提示且**点名两个内核**；
3. 快照内核为空（旧快照）⇒ 提示"无法确认"，**不当作匹配**；
4. 当前内核未知 ⇒ 无提示（猜一个内核会让提示本身误导）；
5. 提示文案要给出**下一步动作**（重新登录），不能只说"不一致"。
"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.fetching.session_state import SessionSummary
from omnicrawler.gui.views.login_session_logic import (
    cross_engine_notice,
    engine_label,
)


def _summary(engine_family: str, *, readable: bool = True) -> SessionSummary:
    return SessionSummary(
        name="acct-abc",
        account="acct",
        path=Path("acct-abc.playwright.json"),
        modified_at=0.0,
        cookie_count=3,
        domains=("example.test",),
        readable=readable,
        engine_family=engine_family,
    )


def test_no_notice_when_engines_match() -> None:
    """内核一致时不该打扰用户。"""
    assert cross_engine_notice(_summary("chromium"), current_engine="chromium") == ""
    assert cross_engine_notice(_summary("firefox"), current_engine="firefox") == ""


def test_notice_names_both_engines_when_different() -> None:
    """内核不同时必须点名两个内核，让用户知道差在哪。"""
    message = cross_engine_notice(_summary("chromium"), current_engine="firefox")
    assert message, "跨内核复用必须给出提示"
    assert "Chromium" in message, f"提示未点名录制内核：{message}"
    assert "Firefox" in message, f"提示未点名当前内核：{message}"
    assert "重新登录" in message, (
        f"提示只说\u201c不一致\u201d却没给出下一步：{message}"
    )


def test_legacy_snapshot_is_not_treated_as_match() -> None:
    """旧快照（未记录内核）必须提示"无法确认"，不能当作匹配。

    把"未知"当成"相同"，正是静默混用的来源。
    """
    message = cross_engine_notice(_summary(""), current_engine="chromium")
    assert message, "旧快照必须提示无法确认，而不是静默当作一致"
    assert "未记录" in message or "无法确认" in message, (
        f"提示应说明是旧快照：{message}"
    )


def test_no_notice_when_current_engine_unknown() -> None:
    """当前内核探测不出来时不提示——猜一个内核会让提示本身误导。"""
    assert cross_engine_notice(_summary("chromium"), current_engine="") == ""


def test_engine_labels_are_user_facing() -> None:
    """内核显示名是用户可读的，不是内部标识符。"""
    assert engine_label("chromium") == "Chromium"
    assert engine_label("firefox") == "Firefox"
    assert engine_label("webkit") == "WebKit"
    assert engine_label("") == ""
    assert engine_label("nonsense") == ""


def test_engine_family_is_case_insensitive() -> None:
    """大小写不该造成误判。"""
    assert cross_engine_notice(_summary("Chromium"), current_engine="chromium") == ""
    assert cross_engine_notice(_summary("chromium"), current_engine="CHROMIUM") == ""


def test_view_has_engine_hint_widget() -> None:
    """登录会话页必须有承载该提示的控件，且默认可隐藏。"""
    import pytest

    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None

    from omnicrawler.core.config import DEFAULTS, AppConfig, deep_merge
    from omnicrawler.gui.views.login_session import LoginSessionView

    root = Path(".")
    raw = deep_merge(dict(DEFAULTS), {"project": {"name": "t", "workspace": "."}})
    config = AppConfig(Path("c.yaml"), root, raw, root)

    view = LoginSessionView(lambda: config)
    try:
        assert hasattr(view, "_engine_hint"), "缺少内核提示控件"
        # 初始无会话时应隐藏
        view._refresh_engine_hint()
        assert view._engine_hint.isVisible() is False or view._engine_hint.text() == ""
    finally:
        view.deleteLater()
