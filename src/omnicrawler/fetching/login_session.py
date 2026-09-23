"""headed 登录窗口的生命周期状态机（《优化方案》§11.1 U1 一号线）。

路线 A（headed Playwright）
--------------------------

不用 QtWebEngine 内嵌（百 MB 级依赖 + 与爬取引擎不同核 ⇒ 指纹不一致），而是以
``headless=False`` 拉起与爬取**同引擎同指纹**的 Chromium，并把登录态存进与爬取
**同一个** ``storage_state`` 快照（路径真源见 :mod:`omnicrawler.fetching.session_state`）。
因此登录后**无需额外注入**即可被 ``PlaywrightPool`` 复用
（``_new_context`` 已按 context_key 自动加载 storage_state）。

★ 凭据边界（最高优先级）
------------------------

窗口**只用于用户手动登录**，程序既不知道也不参与凭据：

* 本模块**不读、不解析、不返回**任何 cookie 值 —— 公共 API 里没有任何
  "把登录态内容给我"的方法，只有"把登录态写到这个路径"的动作；
* cookie 值**不进日志、不进异常信息、不进状态快照**：快照里只有元数据
  （账户 / 域名 / cookie **计数** / 剩余时间），异常文本过
  :func:`omnicrawler.security.redaction.redact_url` 脱敏；
* 落盘是 Playwright 直接写目标文件（**原子 replace + 0600**），不经过
  Python 侧的数据结构；明文 JSON 是《优化方案》§11.1 的**既有裁定**
  （首期 0600 + 路径隔离；AES-GCM 列 U 线二期，并在用户指南显式声明）。

★ 幂等收尾**单入口**
--------------------

用户点「保存并关闭」、用户**直接关掉浏览器窗口**、以及**等待超时**三条路径
最终都走 :meth:`LoginSessionManager.finalize`（§11.6 风险表首行"关闭事件与超时保存
共用一条路径"）。幂等由**阶段推进**保证而非额外的布尔标志：``finalize`` 只在
``launching`` / ``waiting_login`` 阶段动作，且**先**把阶段切到 ``saving`` 再落盘 ——
于是后到的收尾路径（关窗事件、超时定时器、重复点击）看到的都是
``saving`` / ``saved``，直接返回，**不会保存第二次**。
（刻意不加"额外守卫标志"：在只有 GUI 线程驱动的前提下，那种标志不可能被触发，
属 §11.4 禁止的"只会在代码里成立"的检查。）

★ 为什么还需要"周期快照"
------------------------

Playwright 的 context 随浏览器窗口一起消失：用户**直接关窗**后再调
``context.storage_state()`` 必然失败。所以等待期间由 GUI 定时器调用
:meth:`LoginSessionManager.poll`，把最近的登录态先落盘 —— 这样"关窗"这一
竞态不会丢登录态，只是丢最后几秒的增量。
"""

from __future__ import annotations

import importlib.util
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

from ..core.config import AppConfig
from ..core.errors import OmniCrawlError
from ..security.policy import NetworkTargetPolicy
from ..security.redaction import redact_url
from . import session_state
from .browser_launch import build_launch_args

__all__ = [
    "DEFAULT_LOGIN_TIMEOUT_SECONDS",
    "LoginCapture",
    "LoginLauncher",
    "LoginPhase",
    "LoginSessionError",
    "LoginSessionManager",
    "LoginSessionSnapshot",
    "PlaywrightLoginLauncher",
    "playwright_available",
]

DEFAULT_LOGIN_TIMEOUT_SECONDS = 15 * 60
"""默认等待超时 15 分钟（§11.1 裁定 3）。可调，且可被「延长」反复续期。"""

_LOGIN_SCHEMES = frozenset({"http", "https"})
_MAX_MESSAGE_LENGTH = 300


class LoginPhase(StrEnum):
    """登录窗口状态机（取值与 §11.1 的 ``idle→launching→waiting_login→saving→saved/failed`` 一致）。"""

    IDLE = "idle"
    LAUNCHING = "launching"
    WAITING_LOGIN = "waiting_login"
    SAVING = "saving"
    SAVED = "saved"
    FAILED = "failed"


class LoginSessionError(OmniCrawlError):
    """登录会话流程错误（状态机误用、地址非法、窗口打开失败）。"""

    code = "login_session_error"
    suggestion = (
        "请检查要登录的站点地址是否可访问、任务是否配置了 session.persist_cookies=true，"
        "以及当前是否已有一个登录窗口尚未收尾。"
    )


@dataclass(frozen=True, slots=True)
class LoginCapture:
    """一次快照的**元数据**结果。★ 刻意不含任何 cookie 值。"""

    cookie_count: int
    domains: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LoginSessionSnapshot:
    """状态机的**可展示**快照 —— 界面上要显示的全部信息。

    ★ 只含元数据：账户、快照**路径**、cookie **计数**、域名、剩余时间。
    没有 cookie 值，也没有原始 context key（后者可能内嵌代理用户名口令）。
    """

    phase: LoginPhase
    account: str
    session_name: str
    state_path: str
    login_url: str
    remaining_seconds: float
    cookie_count: int
    domains: tuple[str, ...]
    message: str
    capture_ok: bool = False


class LoginLauncher(Protocol):
    """可注入的登录窗口驱动接口（真实实现见 :class:`PlaywrightLoginLauncher`）。

    测试只需实现本协议即可完全离线驱动状态机（headed 行为无法离屏自动化）。
    """

    def open(
        self,
        *,
        url: str,
        account: str,
        proxy: str,
        storage_state_path: Path,
    ) -> None:
        """拉起窗口并导航到 ``url``。失败抛异常。"""

    def capture(self, storage_state_path: Path) -> LoginCapture:
        """把当前登录态**原子写**到 ``storage_state_path``，返回元数据。"""

    def is_open(self) -> bool:
        """窗口是否仍然存在（用户直接关窗 ⇒ False）。"""

    def close(self) -> None:
        """收尾并释放资源（幂等、不抛错）。"""


def playwright_available() -> bool:
    """Playwright 是否可导入 —— U3 用它决定是否显示「未安装」降级引导。

    用 ``find_spec`` 而不是 ``import``：只探测、不触发模块副作用。
    """
    try:
        return importlib.util.find_spec("playwright") is not None
    except (ImportError, ValueError):
        return False


def _safe_message(exc: BaseException) -> str:
    """异常文本 → 可安全展示的一行（URL 内嵌凭据脱敏 + 截断）。"""
    text = redact_url(str(exc)).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    return text[:_MAX_MESSAGE_LENGTH]


class PlaywrightLoginLauncher:
    """路线 A 的真实实现：headed Chromium，与爬取共享启动参数与上下文配置。

    ``factory`` 可注入（测试用假 playwright 对象验证「必定 headed」「启动参数与
    爬取同源」「代理进 new_context」这些**逻辑层**事实）；默认惰性导入
    ``playwright.sync_api`` —— 可选依赖，不在导入期失败。
    """

    def __init__(self, config: AppConfig, *, factory: Callable[[], Any] | None = None) -> None:
        self._config = config
        self._factory = factory
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None

    # ── LoginLauncher 协议 ────────────────────────────────
    def open(
        self,
        *,
        url: str,
        account: str,
        proxy: str,
        storage_state_path: Path,
    ) -> None:
        browser_config = self._config.section("browser")
        verify_tls = bool(self._config.section("http").get("verify_tls", True))
        # ★ 启动参数与爬取路径**共用同一份真源**；headed 时 headless=False ⇒ 不追加无头保真参数。
        launch_args = build_launch_args(
            browser_config.get("launch_args", []),
            headless=False,
            verify_tls=verify_tls,
        )
        user_agent = self._config.section("http").get("user_agent")
        manager = self._factory() if self._factory is not None else _default_sync_playwright()
        playwright = manager.start()
        try:
            browser = playwright.chromium.launch(headless=False, args=launch_args)
            options: dict[str, Any] = {"user_agent": user_agent}
            if storage_state_path.is_file():
                options["storage_state"] = str(storage_state_path)
            if proxy:
                options["proxy"] = {"server": proxy}
            context = browser.new_context(**options)
            page = context.new_page()
            timeout_ms = int(float(self._config.section("http").get("timeout_seconds", 25)) * 1000)
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        except BaseException:
            _close_quietly(playwright)
            raise
        self._playwright = playwright
        self._browser = browser
        self._context = context

    def capture(self, storage_state_path: Path) -> LoginCapture:
        context = self._context
        if context is None:
            raise LoginSessionError("登录窗口已关闭，无法再读取登录态。")
        # ★ cookies 只在**本函数内**存活：只取计数与域名，值不参与返回、日志或异常。
        cookies = context.cookies()
        storage_state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = storage_state_path.with_name(storage_state_path.name + ".tmp")
        try:
            context.storage_state(path=str(tmp))
            os.replace(tmp, storage_state_path)
        finally:
            tmp.unlink(missing_ok=True)
        _chmod_owner_only(storage_state_path)
        domains = tuple(
            sorted({str(item.get("domain", "")) for item in cookies if item.get("domain")})
        )
        return LoginCapture(cookie_count=len(cookies), domains=domains)

    def is_open(self) -> bool:
        browser = self._browser
        if browser is None:
            return False
        try:
            return bool(browser.is_connected())
        except Exception:  # noqa: BLE001 —— 探测失败按"已关闭"处理，交由收尾逻辑兜底
            return False

    def close(self) -> None:
        context, browser, playwright = self._context, self._browser, self._playwright
        self._context = self._browser = self._playwright = None
        for closer in (
            getattr(context, "close", None),
            getattr(browser, "close", None),
            getattr(playwright, "stop", None),
        ):
            if closer is None:
                continue
            try:
                closer()
            except Exception:  # noqa: BLE001 —— 收尾尽力而为：关窗竞态下重复关闭必然抛错
                continue


class LoginSessionManager:
    """登录窗口的生命周期与状态机（GUI 无关，可由离屏测试完全驱动）。

    线程模型：**只在 GUI 线程调用**（``begin`` / ``poll`` / ``finalize`` / ``extend``）。
    内部加锁只是为了状态读取的一致性，不承诺跨线程驱动。
    """

    def __init__(
        self,
        config: AppConfig,
        *,
        launcher: LoginLauncher | None = None,
        policy: NetworkTargetPolicy | None = None,
        timeout_seconds: float = DEFAULT_LOGIN_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._config = config
        self._policy = policy if policy is not None else NetworkTargetPolicy(config)
        self._launcher = launcher
        self._timeout_seconds = max(1.0, float(timeout_seconds))
        self._clock = clock
        self._lock = threading.RLock()
        self._phase = LoginPhase.IDLE
        self._account = ""
        self._session_name = ""
        self._state_path: Path | None = None
        self._login_url = ""
        self._deadline = 0.0
        self._cookie_count = 0
        self._domains: tuple[str, ...] = ()
        self._message = ""
        self._capture_ok = False
        self._saved_once = False

    # ── 只读属性 ──────────────────────────────────────────
    @property
    def phase(self) -> LoginPhase:
        with self._lock:
            return self._phase

    @property
    def session_name(self) -> str:
        """当前（或最近一次）会话的**安全文件名主体**（``账户前缀-身份摘要``）。

        代理（可能内嵌用户名口令）**不进这个名字**，见
        :func:`omnicrawler.fetching.session_state.session_name`。
        """
        with self._lock:
            return self._session_name

    @property
    def state_path(self) -> Path | None:
        with self._lock:
            return self._state_path

    def snapshot(self) -> LoginSessionSnapshot:
        with self._lock:
            remaining = 0.0
            if self._phase is LoginPhase.WAITING_LOGIN:
                remaining = max(0.0, self._deadline - self._clock())
            return LoginSessionSnapshot(
                phase=self._phase,
                account=self._account,
                session_name=self._session_name,
                state_path=str(self._state_path) if self._state_path is not None else "",
                login_url=self._login_url,
                remaining_seconds=remaining,
                cookie_count=self._cookie_count,
                domains=self._domains,
                message=self._message,
                capture_ok=self._capture_ok,
            )

    # ── 流程 ──────────────────────────────────────────────
    def begin(self, url: str, *, account: str = "", proxy: str = "") -> LoginSessionSnapshot:
        """校验地址与代理 → 拉起窗口 → 进入 ``waiting_login``。"""
        with self._lock:
            if self._phase in {LoginPhase.LAUNCHING, LoginPhase.WAITING_LOGIN, LoginPhase.SAVING}:
                raise LoginSessionError("已有登录会话正在进行；请先收尾（保存并关闭）当前窗口。")
        target = self._validate_target(url, proxy)
        account_name = (account or "").strip() or str(
            self._config.section("session").get("name", "default")
        )
        key = session_state.context_key(account=account_name, proxy=proxy)
        state_path = session_state.require_session_state_path(self._config, key)
        with self._lock:
            self._phase = LoginPhase.LAUNCHING
            self._account = account_name
            self._session_name = session_state.session_name(key)
            self._state_path = state_path
            self._login_url = redact_url(target)
            self._deadline = 0.0
            self._cookie_count = 0
            self._domains = ()
            self._message = ""
            self._capture_ok = False
            self._saved_once = False
        try:
            self._ensure_launcher().open(
                url=target, account=account_name, proxy=proxy, storage_state_path=state_path
            )
        except BaseException as exc:
            with self._lock:
                self._phase = LoginPhase.FAILED
                self._message = f"登录窗口打开失败：{_safe_message(exc)}"
            raise LoginSessionError(self._message) from exc
        with self._lock:
            self._phase = LoginPhase.WAITING_LOGIN
            self._deadline = self._clock() + self._timeout_seconds
            self._message = "请在打开的浏览器窗口中手动完成登录，然后点「保存并关闭」。"
            return self.snapshot()

    def extend(self, *, seconds: float | None = None) -> LoginSessionSnapshot:
        """延长等待（默认再给一个完整超时窗口，§11.1 裁定 3）。"""
        with self._lock:
            if self._phase is not LoginPhase.WAITING_LOGIN:
                raise LoginSessionError("当前没有等待中的登录窗口，无法延长。")
            bonus = self._timeout_seconds if seconds is None else max(1.0, float(seconds))
            self._deadline = self._clock() + bonus
            return self.snapshot()

    def is_expired(self) -> bool:
        with self._lock:
            if self._phase is not LoginPhase.WAITING_LOGIN:
                return False
            return self._clock() >= self._deadline

    def is_open(self) -> bool:
        with self._lock:
            launcher = self._launcher
            active = self._phase in {LoginPhase.LAUNCHING, LoginPhase.WAITING_LOGIN}
        if launcher is None or not active:
            return False
        try:
            return bool(launcher.is_open())
        except Exception:  # noqa: BLE001
            return False

    def poll(self) -> LoginSessionSnapshot | None:
        """GUI 定时器周期调用：更新剩余时间、兜底快照、检测关窗、到点先保存再关闭。

        返回 ``None`` 表示"当前没有进行中的会话"（无状态变化需要刷新界面）。
        """
        with self._lock:
            if self._phase is not LoginPhase.WAITING_LOGIN:
                return None
        if not self.is_open():
            # 用户直接关掉了浏览器窗口：state 已随 context 消失，用最后一次兜底快照收尾。
            return self.finalize(reason="window_closed")
        if self.is_expired():
            # ★ 裁定 3：到点**先保存再关闭**，超时不丢登录态。
            return self.finalize(reason="timeout")
        return self.capture()

    def capture(self) -> LoginSessionSnapshot:
        """把当前登录态快照落盘（等待期间的安全网；也用作收尾的一次保存）。"""
        with self._lock:
            if self._phase not in {LoginPhase.WAITING_LOGIN, LoginPhase.SAVING}:
                return self.snapshot()
            launcher = self._launcher
            state_path = self._state_path
        if launcher is None or state_path is None:
            return self.snapshot()
        try:
            captured = launcher.capture(state_path)
        except BaseException as exc:  # noqa: BLE001 —— 兜底快照失败不中断会话，但要留痕
            with self._lock:
                self._capture_ok = False
                self._message = f"登录态快照写入失败：{_safe_message(exc)}"
                return self.snapshot()
        with self._lock:
            self._capture_ok = True
            self._saved_once = True
            self._cookie_count = int(captured.cookie_count)
            self._domains = tuple(captured.domains)
            return self.snapshot()

    def finalize(self, *, reason: str = "user_closed") -> LoginSessionSnapshot:
        """★ **幂等收尾单入口** —— 「保存并关闭」「用户关窗」「等待超时」三条路径共用。

        幂等靠**阶段推进**：先切 ``saving`` 再落盘 ⇒ 后到的收尾路径看到
        ``saving`` / ``saved`` 即返回，不会保存第二次。
        """
        with self._lock:
            if self._phase not in {LoginPhase.LAUNCHING, LoginPhase.WAITING_LOGIN}:
                return self.snapshot()
            self._phase = LoginPhase.SAVING
        captured = self.capture()
        self._close_launcher()
        with self._lock:
            # 关窗竞态：最后一次 capture 失败（context 已消失）但此前有过成功快照
            # ⇒ 登录态其实在盘上，不能报"失败"误导用户重登一次。
            self._phase = LoginPhase.SAVED if (captured.capture_ok or self._saved_once) else LoginPhase.FAILED
            if self._phase is LoginPhase.SAVED:
                self._message = _SAVED_MESSAGES.get(reason, _SAVED_MESSAGES["user_closed"])
            elif not self._message:
                self._message = "登录会话未能保存。"
            return self.snapshot()

    # ── 内部 ──────────────────────────────────────────────
    def _ensure_launcher(self) -> LoginLauncher:
        if self._launcher is None:
            self._launcher = PlaywrightLoginLauncher(self._config)
        return self._launcher

    def _close_launcher(self) -> None:
        with self._lock:
            launcher = self._launcher
        if launcher is None:
            return
        try:
            launcher.close()
        except Exception:  # noqa: BLE001 —— 收尾尽力而为
            return

    def _validate_target(self, url: str, proxy: str) -> str:
        """地址规范化 + 目标策略校验（与爬取侧同一把尺子）。"""
        candidate = (url or "").strip()
        if not candidate:
            raise LoginSessionError("请填写要登录的站点地址。")
        parts = urlsplit(candidate)
        if parts.scheme and parts.scheme not in _LOGIN_SCHEMES:
            raise LoginSessionError(f"只支持 http/https 站点地址，收到：{parts.scheme}://")
        if not parts.scheme:
            candidate = f"https://{candidate}"
        self._policy.require(candidate)
        if proxy:
            # ★ 登录窗口与任务代理**同源**（§11.6 风险表第 2 行）：同一把策略尺子。
            self._policy.require(proxy)
        return candidate


_SAVED_MESSAGES = {
    "user_closed": "登录会话已保存，后续采集可直接复用。",
    "save_and_close": "登录会话已保存，后续采集可直接复用。",
    "window_closed": "登录窗口已关闭，使用最后一次自动保存的登录态。",
    "timeout": "等待超时：已先保存登录态再关闭窗口。",
}


def _default_sync_playwright() -> Any:
    """惰性导入 Playwright（可选依赖：导入期不失败，缺包时由调用方给降级文案）。"""
    from playwright.sync_api import sync_playwright

    return sync_playwright()


def _close_quietly(target: Any) -> None:
    closer = getattr(target, "stop", None)
    if closer is None:
        return
    try:
        closer()
    except Exception:  # noqa: BLE001
        return


def _chmod_owner_only(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        return  # Windows 无 POSIX 权限语义
