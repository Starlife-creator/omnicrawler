"""U1 一号线：headed 登录窗口的**逻辑层**验收。

headed 行为无法离屏自动化（《优化方案》§11.1：实机验证归维护者），因此本文件只覆盖
可以确定性判定的部分：

* **路径唯一真源** —— 登录管理器与 ``PlaywrightPool`` 对同一输入产出**同一路径**
  （并配一条"不同输入必须不同"的反向对照，证明上一条不是恒真断言）；
* 状态机 ``idle→launching→waiting_login→saving→saved/failed``；
* **幂等收尾单入口** —— 三条路径共用一个入口，重复触发只保存一次；
* 超时**先保存再关闭**、关窗竞态不误报失败；
* 代理与目标地址过 ``NetworkTargetPolicy``（与爬取同一把尺子，fail-closed）；
* ★ **凭据边界** —— cookie 值不进日志 / 不进异常 / 不进状态快照；
* 真实 launcher 的**参数层事实**（必定 headed、启动参数与爬取同源、代理进 context、
  快照原子写）用假 playwright 驱动，不依赖真实浏览器。
"""

from __future__ import annotations

import json
import logging
import os
import stat
from pathlib import Path
from typing import Any

import pytest

from omnicrawler.core.config import AppConfig, load_config
from omnicrawler.core.models import CrawlRequest
from omnicrawler.fetching.browser_launch import build_launch_args
from omnicrawler.fetching.browser_pool import PlaywrightPool
from omnicrawler.fetching.login_session import (
    DEFAULT_LOGIN_TIMEOUT_SECONDS,
    LoginCapture,
    LoginPhase,
    LoginSessionError,
    LoginSessionManager,
    PlaywrightLoginLauncher,
    playwright_available,
)
from omnicrawler.fetching.session_state import (
    SessionPersistenceDisabledError,
    session_name,
    storage_state_filename,
)
from omnicrawler.security.policy import NetworkTargetPolicy, PolicyBlockedError

_SECRET = "SECRET-TOKEN-VALUE-DO-NOT-LOG"


def _config(
    tmp_path: Path,
    *,
    persist: bool = True,
    proxy: str = "",
    name: str = "default",
    extra_http: str = "",
    extra_browser: str = "",
) -> AppConfig:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: u1, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        f"session: {{persist_cookies: {str(persist).lower()}, name: {name!r}}}\n"
        f"http: {{proxy: {proxy!r}{extra_http}}}\n"
        f"browser: {{{extra_browser}}}\n",
        encoding="utf-8",
    )
    return load_config(config_path)


def _policy(config: AppConfig) -> NetworkTargetPolicy:
    """离线策略：保留私网/方案判定，桩掉 DNS 钉扎解析（假域名无 A 记录）。"""
    policy = NetworkTargetPolicy(config)
    policy.approved_addresses = lambda host, port: (host, port)  # type: ignore[method-assign]
    return policy


class _FakeLauncher:
    """login 窗口的测试替身：完全离线、可注入故障。"""

    def __init__(
        self,
        *,
        cookie_count: int = 3,
        domains: tuple[str, ...] = ("example.org",),
        open_error: BaseException | None = None,
    ) -> None:
        self.cookie_count = cookie_count
        self.domains = domains
        self.open_error = open_error
        self.opened: dict[str, Any] | None = None
        self.capture_calls: list[Path] = []
        self.close_calls = 0
        self.open_window = True
        self.capture_error: BaseException | None = None

    def open(self, *, url: str, account: str, proxy: str, storage_state_path: Path) -> None:
        if self.open_error is not None:
            raise self.open_error
        self.opened = {
            "url": url,
            "account": account,
            "proxy": proxy,
            "storage_state_path": storage_state_path,
        }
        self.open_window = True

    def capture(self, storage_state_path: Path) -> LoginCapture:
        self.capture_calls.append(storage_state_path)
        if self.capture_error is not None:
            raise self.capture_error
        storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        storage_state_path.write_text(
            json.dumps({"cookies": [{"name": "sid", "value": _SECRET, "domain": "example.org"}]}),
            encoding="utf-8",
        )
        return LoginCapture(cookie_count=self.cookie_count, domains=self.domains)

    def is_open(self) -> bool:
        return self.open_window

    def close(self) -> None:
        self.close_calls += 1
        self.open_window = False


def _manager(
    config: AppConfig,
    launcher: _FakeLauncher | None = None,
    *,
    timeout_seconds: float = DEFAULT_LOGIN_TIMEOUT_SECONDS,
    clock: Any = None,
) -> LoginSessionManager:
    kwargs: dict[str, Any] = {
        "launcher": launcher if launcher is not None else _FakeLauncher(),
        "policy": _policy(config),
        "timeout_seconds": timeout_seconds,
    }
    if clock is not None:
        kwargs["clock"] = clock
    return LoginSessionManager(config, **kwargs)


def _pool_state_path(config: AppConfig, request: CrawlRequest) -> Path | None:
    pool = object.__new__(PlaywrightPool)
    pool.config = config
    return pool._state_path(pool._context_key(request))


# ── 1. 路径唯一真源 ──────────────────────────────────────


def test_login_target_path_is_identical_to_browser_pool(tmp_path: Path) -> None:
    """★ 真源一致性：登录保存的会话必须落在爬取会去读的那个文件上。"""
    config = _config(tmp_path)
    manager = _manager(config)
    snapshot = manager.begin("https://example.org/")

    expected = _pool_state_path(config, CrawlRequest("https://example.org/"))
    assert expected is not None
    assert snapshot.state_path == str(expected)
    assert manager.state_path == expected


def test_login_target_path_follows_account_and_proxy(tmp_path: Path) -> None:
    """反向对照：换个账户/代理**必须**换文件 —— 证明上一条不是恒真断言。"""
    config = _config(tmp_path)
    manager = _manager(config)
    snapshot = manager.begin("https://example.org/", account="alice")

    baseline = _pool_state_path(config, CrawlRequest("https://example.org/"))
    assert snapshot.state_path != str(baseline)
    alice = _pool_state_path(
        config, CrawlRequest("https://example.org/", meta={"account": "alice"})
    )
    assert snapshot.state_path == str(alice)


@pytest.mark.parametrize(
    "key",
    [
        "default|",
        "alice|http://user:pass@proxy.example:8080",
        "域|代理",
        "x" * 200,
        "",
    ],
)
def test_session_name_is_deterministic_and_credential_free(key: str) -> None:
    """★ 文件名必须**确定性**、可区分，且**不得把代理凭据写进文件名**。"""
    name = session_name(key)
    assert name == session_name(key)  # 确定性
    assert name.isascii()
    for forbidden in (":", "/", "@", "|", "\\", "?"):
        assert forbidden not in name
    # 旧规则的产物：代理的用户名口令会字面出现在文件名里 —— 现在必须没有
    assert "user" not in name
    assert "pass" not in name
    assert storage_state_filename(key) == f"{name}.playwright.json"


def test_distinct_identities_never_collide_on_filename() -> None:
    """不同身份键**必须**得到不同文件名（摘要保证），否则两份会话会互相覆盖。"""
    keys = [
        "default|",
        "alice|",
        "default|http://proxy.example:8080",
        "default|http://bob:hunter2@proxy.example:8080",
        "default|http://bob:other@proxy.example:8080",
    ]
    names = [session_name(key) for key in keys]
    assert len(set(names)) == len(keys)


def test_session_name_keeps_readable_account_prefix() -> None:
    """摘要之外要留可读账户前缀（用户要能在列表里认出是哪个账户）。"""
    name = session_name("alice|http://bob:hunter2@proxy.example:8080")
    assert name.startswith("alice-")
    assert "hunter2" not in name
    assert len(name.split("-", 1)[1]) == 12


def test_persistence_disabled_is_explicit_error(tmp_path: Path) -> None:
    """★ 判据纪律：无处落盘要**显式报错**，不得静默"登录了但没保存"。"""
    config = _config(tmp_path, persist=False)
    launcher = _FakeLauncher()
    manager = _manager(config, launcher)

    with pytest.raises(SessionPersistenceDisabledError):
        manager.begin("https://example.org/")

    assert launcher.opened is None
    assert _pool_state_path(config, CrawlRequest("https://example.org/")) is None


# ── 2. 状态机 ────────────────────────────────────────────


def test_phase_sequence_and_snapshot_metadata(tmp_path: Path) -> None:
    config = _config(tmp_path)
    launcher = _FakeLauncher(cookie_count=7, domains=("a.example", "b.example"))
    manager = _manager(config, launcher)

    assert manager.phase is LoginPhase.IDLE
    started = manager.begin("example.org")

    assert started.phase is LoginPhase.WAITING_LOGIN
    assert started.account == "default"
    assert started.login_url == "https://example.org"  # 缺 scheme 时补 https
    assert started.remaining_seconds == pytest.approx(DEFAULT_LOGIN_TIMEOUT_SECONDS)
    assert launcher.opened is not None and launcher.opened["url"] == "https://example.org"

    captured = manager.poll()
    assert captured is not None and captured.capture_ok
    assert captured.cookie_count == 7
    assert captured.domains == ("a.example", "b.example")

    finished = manager.finalize()
    assert finished.phase is LoginPhase.SAVED
    assert finished.capture_ok


def test_phase_enum_matches_plan_definition() -> None:
    assert [phase.value for phase in LoginPhase] == [
        "idle",
        "launching",
        "waiting_login",
        "saving",
        "saved",
        "failed",
    ]


def test_second_begin_is_rejected_while_session_active(tmp_path: Path) -> None:
    config = _config(tmp_path)
    manager = _manager(config)
    manager.begin("https://example.org/")

    with pytest.raises(LoginSessionError):
        manager.begin("https://example.org/")


def test_open_failure_marks_failed_and_redacts_proxy_credentials(tmp_path: Path) -> None:
    config = _config(tmp_path)
    launcher = _FakeLauncher(
        open_error=RuntimeError("launch failed for http://alice:s3cret@proxy.example:8080")
    )
    manager = _manager(config, launcher)

    with pytest.raises(LoginSessionError) as error:
        manager.begin("https://example.org/")

    assert "s3cret" not in str(error.value)
    assert "<redacted>" in str(error.value)
    assert manager.phase is LoginPhase.FAILED


# ── 3. 幂等收尾单入口 ────────────────────────────────────


def test_finalize_is_idempotent_single_entry(tmp_path: Path) -> None:
    """★ 「保存并关闭」与「超时保存」共用一条路径；重复触发只保存一次。"""
    config = _config(tmp_path)
    launcher = _FakeLauncher()
    manager = _manager(config, launcher)
    manager.begin("https://example.org/")

    first = manager.finalize(reason="save_and_close")
    second = manager.finalize(reason="timeout")
    # 第三条路径：超时定时器在收尾之后又跑了一次 poll
    late_poll = manager.poll()

    assert first.phase is LoginPhase.SAVED
    assert second.phase is LoginPhase.SAVED
    assert late_poll is None
    assert len(launcher.capture_calls) == 1
    assert launcher.close_calls == 1


def test_timeout_saves_before_closing(tmp_path: Path) -> None:
    config = _config(tmp_path)
    launcher = _FakeLauncher()
    now = [0.0]
    manager = _manager(config, launcher, timeout_seconds=900, clock=lambda: now[0])
    manager.begin("https://example.org/")

    now[0] = 901.0
    snapshot = manager.poll()

    assert snapshot is not None
    assert snapshot.phase is LoginPhase.SAVED
    assert "超时" in snapshot.message
    assert launcher.capture_calls, "到点必须先保存再关闭"
    assert launcher.close_calls == 1


def test_window_closed_uses_last_good_snapshot(tmp_path: Path) -> None:
    """用户直接关窗 ⇒ context 已消失，最后一次兜底快照仍应算成功。

    ``capture_ok`` 的语义是"**最后一次**写入尝试是否成功"，所以这里是 False；
    终态由"此前是否成功存过"决定 —— 不能因为收尾那次读不到而谎报失败。
    """
    config = _config(tmp_path)
    launcher = _FakeLauncher()
    manager = _manager(config, launcher)
    manager.begin("https://example.org/")
    assert manager.poll() is not None  # 等待期间的安全网快照

    launcher.capture_error = RuntimeError("Target page, context or browser has been closed")
    launcher.open_window = False
    snapshot = manager.poll()

    assert snapshot is not None
    assert snapshot.phase is LoginPhase.SAVED
    assert snapshot.capture_ok is False
    assert snapshot.cookie_count == 3  # 沿用最后一次成功快照的元数据
    assert "最后一次" in snapshot.message


def test_window_closed_without_any_snapshot_fails(tmp_path: Path) -> None:
    """反向对照：一次快照都没成功过 ⇒ 必须报失败，不得谎报已保存。"""
    config = _config(tmp_path)
    launcher = _FakeLauncher()
    manager = _manager(config, launcher)
    manager.begin("https://example.org/")

    launcher.capture_error = RuntimeError("context gone")
    launcher.open_window = False
    snapshot = manager.poll()

    assert snapshot is not None
    assert snapshot.phase is LoginPhase.FAILED


def test_extend_postpones_deadline(tmp_path: Path) -> None:
    config = _config(tmp_path)
    now = [0.0]
    manager = _manager(config, timeout_seconds=900, clock=lambda: now[0])
    manager.begin("https://example.org/")

    now[0] = 800.0
    assert manager.snapshot().remaining_seconds == pytest.approx(100.0)

    after_extend = manager.extend()
    assert after_extend.remaining_seconds == pytest.approx(900.0)
    assert manager.is_expired() is False


def test_extend_requires_active_window(tmp_path: Path) -> None:
    manager = _manager(_config(tmp_path))
    with pytest.raises(LoginSessionError):
        manager.extend()


def test_poll_returns_none_when_idle(tmp_path: Path) -> None:
    manager = _manager(_config(tmp_path))
    assert manager.poll() is None


# ── 4. 策略校验（与爬取同一把尺子）────────────────────────


def test_proxy_goes_through_the_same_policy(tmp_path: Path) -> None:
    config = _config(tmp_path)
    launcher = _FakeLauncher()
    manager = _manager(config, launcher)

    with pytest.raises(PolicyBlockedError):
        manager.begin("https://example.org/", proxy="http://127.0.0.1:7890")

    assert launcher.opened is None


def test_private_proxy_allowed_only_with_explicit_switch(tmp_path: Path) -> None:
    config = _config(tmp_path, extra_http=", allow_private_network: true")
    launcher = _FakeLauncher()
    manager = _manager(config, launcher)

    snapshot = manager.begin("https://example.org/", proxy="http://127.0.0.1:7890")

    assert snapshot.phase is LoginPhase.WAITING_LOGIN
    assert launcher.opened is not None
    assert launcher.opened["proxy"] == "http://127.0.0.1:7890"


def test_non_http_scheme_is_rejected(tmp_path: Path) -> None:
    manager = _manager(_config(tmp_path))
    with pytest.raises(LoginSessionError):
        manager.begin("ftp://example.org/")


def test_blank_url_is_rejected(tmp_path: Path) -> None:
    manager = _manager(_config(tmp_path))
    with pytest.raises(LoginSessionError):
        manager.begin("   ")


# ── 5. ★ 凭据边界 ────────────────────────────────────────


def test_no_cookie_value_reaches_logs_or_snapshot(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """★ 最高优先级红线：cookie 值不得出现在日志 / 状态快照（异常路径另测）。"""
    config = _config(tmp_path)
    launcher = _FakeLauncher(cookie_count=1, domains=("example.org",))
    manager = _manager(config, launcher)

    with caplog.at_level(logging.DEBUG):
        manager.begin("https://example.org/")
        manager.poll()
        snapshot = manager.finalize()

    assert _SECRET not in caplog.text
    assert _SECRET not in repr(snapshot)
    # 元数据本身仍然可用（否则上面两条会"因为什么都没做"而恒真）
    assert snapshot.cookie_count == 1
    assert snapshot.domains == ("example.org",)
    assert snapshot.state_path.endswith(".playwright.json")
    assert f"{os.sep}sessions{os.sep}" in snapshot.state_path


def test_snapshot_exposes_no_raw_context_key(tmp_path: Path) -> None:
    """快照不得回吐 ``account|proxy`` 原串（代理可能内嵌用户名口令）。"""
    config = _config(tmp_path)
    launcher = _FakeLauncher()
    manager = _manager(config, launcher)
    snapshot = manager.begin(
        "https://example.org/", account="alice", proxy="http://bob:hunter2@proxy.example:8080"
    )

    assert "hunter2" not in repr(snapshot)
    assert "|" not in snapshot.session_name
    assert snapshot.session_name.startswith("alice-")


# ── 6. 真实 launcher 的参数层事实（假 playwright 驱动）────


class _FakePage:
    def __init__(self) -> None:
        self.goto_calls: list[tuple[str, dict[str, Any]]] = []

    def goto(self, url: str, **kwargs: Any) -> None:
        self.goto_calls.append((url, kwargs))


class _FakeContext:
    def __init__(self, options: dict[str, Any]) -> None:
        self.options = options
        self.closed = False
        self.pages: list[_FakePage] = []
        self.state_calls: list[str] = []

    def new_page(self) -> _FakePage:
        page = _FakePage()
        self.pages.append(page)
        return page

    def cookies(self) -> list[dict[str, Any]]:
        return [{"name": "sid", "value": _SECRET, "domain": "example.org"}]

    def storage_state(self, *, path: str) -> None:
        self.state_calls.append(path)
        Path(path).write_text(json.dumps({"cookies": [{"name": "sid", "value": _SECRET}]}), "utf-8")

    def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self) -> None:
        self.context_options: dict[str, Any] | None = None
        self.context: _FakeContext | None = None
        self.connected = True
        self.closed = False

    def new_context(self, **options: Any) -> _FakeContext:
        self.context_options = options
        self.context = _FakeContext(options)
        return self.context

    def is_connected(self) -> bool:
        return self.connected

    def close(self) -> None:
        self.closed = True


class _FakeChromium:
    def __init__(self, browser: _FakeBrowser) -> None:
        self._browser = browser
        self.launch_kwargs: dict[str, Any] | None = None

    def launch(self, **kwargs: Any) -> _FakeBrowser:
        self.launch_kwargs = kwargs
        return self._browser


class _FakePlaywright:
    def __init__(self) -> None:
        self.browser = _FakeBrowser()
        self.chromium = _FakeChromium(self.browser)
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _FakeManager:
    def __init__(self, playwright: _FakePlaywright) -> None:
        self._playwright = playwright

    def start(self) -> _FakePlaywright:
        return self._playwright


def _real_launcher(config: AppConfig) -> tuple[PlaywrightLoginLauncher, _FakePlaywright]:
    playwright = _FakePlaywright()
    launcher = PlaywrightLoginLauncher(config, factory=lambda: _FakeManager(playwright))
    return launcher, playwright


def test_real_launcher_is_headed_and_shares_launch_args(tmp_path: Path) -> None:
    """headed 是硬事实；启动参数必须与爬取**同源**（且不得混入无头保真参数）。"""
    config = _config(tmp_path, extra_browser='launch_args: ["--window-size=1280,900"]')
    launcher, playwright = _real_launcher(config)
    path = tmp_path / "work" / "sessions" / "default_.playwright.json"

    launcher.open(url="https://example.org/", account="default", proxy="", storage_state_path=path)

    kwargs = playwright.chromium.launch_kwargs
    assert kwargs is not None
    assert kwargs["headless"] is False
    assert kwargs["args"] == build_launch_args(["--window-size=1280,900"], headless=False, verify_tls=True)
    assert "--disable-background-timer-throttling" not in kwargs["args"]
    assert playwright.browser.context_options is not None
    # 未配置代理 ⇒ 不得凭空塞一个 proxy 进 context
    assert "proxy" not in playwright.browser.context_options
    page = playwright.browser.context.pages[0] if playwright.browser.context else None
    assert page is not None and page.goto_calls[0][0] == "https://example.org/"


def test_real_launcher_passes_proxy_and_seeds_existing_state(tmp_path: Path) -> None:
    config = _config(tmp_path)
    launcher, playwright = _real_launcher(config)
    path = tmp_path / "work" / "sessions" / "default_.playwright.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"cookies": []}', encoding="utf-8")

    launcher.open(
        url="https://example.org/",
        account="default",
        proxy="http://proxy.example:8080",
        storage_state_path=path,
    )

    options = playwright.browser.context_options
    assert options is not None
    assert options["proxy"] == {"server": "http://proxy.example:8080"}
    assert options["storage_state"] == str(path)


def test_real_launcher_capture_is_atomic_and_owner_only(tmp_path: Path) -> None:
    config = _config(tmp_path)
    launcher, playwright = _real_launcher(config)
    path = tmp_path / "work" / "sessions" / "default_.playwright.json"
    launcher.open(
        url="https://example.org/", account="default", proxy="", storage_state_path=path
    )

    capture = launcher.capture(path)

    assert capture == LoginCapture(cookie_count=1, domains=("example.org",))
    context = playwright.browser.context
    assert context is not None
    # ★ 原子写：Playwright 必须写临时文件、由我们 replace —— 直接写目标路径会让
    #   并发读者（爬取侧）读到写了一半的 JSON。
    assert context.state_calls == [str(path.with_name(path.name + ".tmp"))]
    assert path.is_file()
    assert not path.with_name(path.name + ".tmp").exists()
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_real_launcher_capture_keeps_cookie_values_out_of_logs(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """★ 真 launcher 会真的拿到 cookie 值（Playwright 无"只要计数"的 API），
    因此这条守卫必须钉在**真实实现**上：值可以进内存，但绝不能进日志。"""
    config = _config(tmp_path)
    launcher, _ = _real_launcher(config)
    path = tmp_path / "work" / "sessions" / "default_.playwright.json"
    launcher.open(
        url="https://example.org/", account="default", proxy="", storage_state_path=path
    )

    with caplog.at_level(logging.DEBUG):
        capture = launcher.capture(path)

    assert _SECRET not in caplog.text
    # 正对照：守卫不能"因为什么都没做"而恒真
    assert capture.cookie_count == 1
    assert path.is_file()


def test_real_launcher_close_is_idempotent(tmp_path: Path) -> None:
    config = _config(tmp_path)
    launcher, playwright = _real_launcher(config)
    path = tmp_path / "work" / "sessions" / "default_.playwright.json"
    launcher.open(
        url="https://example.org/", account="default", proxy="", storage_state_path=path
    )

    launcher.close()
    launcher.close()  # 第二次不得抛错

    assert playwright.stopped is True
    assert launcher.is_open() is False


def test_playwright_available_is_boolean() -> None:
    assert isinstance(playwright_available(), bool)
