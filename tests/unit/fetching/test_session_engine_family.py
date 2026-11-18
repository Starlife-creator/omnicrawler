"""登录态必须记录录制时的浏览器内核，跨内核复用要能被识别。

## 为什么

登录态与**内核指纹**绑定。用 Chromium 登录拿到的 cookie 到 Firefox 下复用，
UA 与 TLS 指纹不一致，站点可能直接判定会话异常——而现场**毫无线索**：
用户看到的是"我明明登录过，怎么又要登录"。

优化方案 §3.2(b) 要求：

> 已有会话元数据记录所属 `engine_family`，跨内核复用时在界面给出显式提示
> 而非静默混用。

## 判据

1. 登录捕获后，快照里带 `_omnicrawler.engine_family`，值等于当时的内核；
2. `summarize_session()` 能把它读回来；
3. **旧快照（无该字段）读出空串**，而不是默认 `chromium`——
   默认会让跨内核复用变静默，正是要防的形态；
4. 元数据存在**自有命名空间**里，不污染 Playwright 的 `cookies` / `origins`。
"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.fetching.session_crypto import load_storage_state, save_storage_state
from omnicrawler.fetching.session_state import (
    SESSION_META_KEY,
    SessionSummary,
    summarize_session,
)


def _write_state(path: Path, payload: dict) -> None:
    save_storage_state(payload, path)


def test_summary_reads_engine_family(tmp_path: Path) -> None:
    """快照里记录了内核，摘要就要读得回来。"""
    snapshot = tmp_path / "acct-abc.playwright.json"
    _write_state(
        snapshot,
        {
            "cookies": [
                {"name": "sid", "value": "x", "domain": "example.test", "expires": -1}
            ],
            "origins": [],
            SESSION_META_KEY: {"engine_family": "firefox"},
        },
    )

    summary = summarize_session(snapshot)
    assert summary.engine_family == "firefox", (
        f"应读出 firefox，实际 {summary.engine_family!r}"
    )


def test_legacy_snapshot_yields_empty_not_chromium(tmp_path: Path) -> None:
    """旧快照（无内核字段）必须读出**空串**，不能默认 chromium。

    默认 chromium 会让"在 Firefox 下复用 Chromium 会话"这件事变得静默，
    而这正是要防的错误形态。
    """
    snapshot = tmp_path / "legacy-abc.playwright.json"
    _write_state(
        snapshot,
        {"cookies": [], "origins": []},  # 完全没有 _omnicrawler 段
    )

    summary = summarize_session(snapshot)
    assert summary.engine_family == "", (
        "旧快照应读出空串（表示未知），而不是猜一个内核"
    )


def test_meta_namespace_does_not_pollute_playwright_fields(tmp_path: Path) -> None:
    """自有元数据必须放 `_omnicrawler` 命名空间，不往顶层塞新键。"""
    snapshot = tmp_path / "acct-abc.playwright.json"
    _write_state(
        snapshot,
        {
            "cookies": [],
            "origins": [],
            SESSION_META_KEY: {"engine_family": "webkit"},
        },
    )

    raw = load_storage_state(snapshot, migrate=False)
    # Playwright 标准字段仍在
    assert "cookies" in raw and "origins" in raw
    # 我们的元数据在命名空间里
    assert isinstance(raw.get(SESSION_META_KEY), dict)
    assert raw[SESSION_META_KEY]["engine_family"] == "webkit"
    # 顶层没有散落的自定义键
    assert set(raw) <= {"cookies", "origins", SESSION_META_KEY}, (
        f"顶层出现了计划外的键：{set(raw) - {'cookies', 'origins', SESSION_META_KEY}}"
    )


def test_summary_field_defaults_to_empty() -> None:
    """`SessionSummary` 的新字段必须有安全默认值（空串）。"""
    summary = SessionSummary(
        name="n",
        account="a",
        path=Path("p"),
        modified_at=0.0,
        cookie_count=0,
        domains=(),
        readable=False,
    )
    assert summary.engine_family == ""


def test_capture_records_engine_family(tmp_path: Path) -> None:
    """登录捕获必须把当前内核写进快照。

    用假 playwright 对象驱动 `PlaywrightLoginLauncher.capture()`，
    不依赖真实浏览器。
    """
    from omnicrawler.core.config import DEFAULTS, AppConfig, deep_merge
    from omnicrawler.fetching.login_session import PlaywrightLoginLauncher

    root = tmp_path
    raw = deep_merge(dict(DEFAULTS), {"project": {"name": "t", "workspace": str(root)}})
    config = AppConfig(root / "c.yaml", root, raw, root)

    snapshot = tmp_path / "acct-abc.playwright.json"

    class _FakeContext:
        def cookies(self) -> list[dict]:
            return [{"name": "sid", "value": "v", "domain": "example.test"}]

        def storage_state(self) -> dict:
            return {"cookies": [], "origins": []}

    launcher = PlaywrightLoginLauncher(config)
    launcher._context = _FakeContext()  # type: ignore[assignment]
    launcher._engine_family = "firefox"

    launcher.capture(snapshot)

    raw_state = load_storage_state(snapshot, migrate=False)
    assert raw_state[SESSION_META_KEY]["engine_family"] == "firefox", (
        "capture 未把当时的内核写进快照"
    )


def test_engine_family_property_exposes_current_engine() -> None:
    """`engine_family` 属性应暴露当前内核（未打开时为空串）。"""
    from omnicrawler.core.config import DEFAULTS, AppConfig, deep_merge
    from omnicrawler.fetching.login_session import PlaywrightLoginLauncher

    raw = deep_merge(dict(DEFAULTS), {})
    config = AppConfig(Path("c.yaml"), Path("."), raw, Path("."))
    launcher = PlaywrightLoginLauncher(config)
    assert launcher.engine_family == ""

    launcher._engine_family = "webkit"
    assert launcher.engine_family == "webkit"


def test_engine_family_is_normalized_lowercase(tmp_path: Path) -> None:
    """读取时归一为小写，避免 `Firefox` / `firefox` 被当成两个内核。"""
    snapshot = tmp_path / "acct-abc.playwright.json"
    _write_state(
        snapshot,
        {"cookies": [], "origins": [], SESSION_META_KEY: {"engine_family": "FireFox"}},
    )
    assert summarize_session(snapshot).engine_family == "firefox"
