from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

LOGGER = logging.getLogger(__name__)


from ..core.config import AppConfig
from ..core.errors import ResponseTooLargeError
from ..core.models import CrawlRequest, FetchResult
from ..runtime.resource_profiles import effective_browser_pool
from ..security.egress import EgressBroker
from ..security.policy import HostRateLimiter, NetworkTargetPolicy
from .browser_engines import (
    BrowserAction as BrowserAction,
)
from .browser_engines import (
    BrowserEngine as BrowserEngine,
)
from .browser_engines import (
    PlaywrightAdapter,
    SeleniumAdapter,
    _action_locator,
    run_actions,
)
from .browser_engines import (
    _dispatch_action as _dispatch_action,
)
from .browser_guards import _Watchdog
from .browser_guards import (
    strip_cross_origin_credentials as strip_cross_origin_credentials,
)
from .browser_pool import PlaywrightPool
from .browser_pool import (
    _PoolTask as _PoolTask,
)

# ---------------------------------------------------------------------------
# Unified browser action protocol
# ---------------------------------------------------------------------------


class BrowserFetcher:
    """Render-engine fetcher that drives Playwright or Selenium via the unified action protocol."""

    def __init__(self, config: AppConfig, limiter=None, egress: EgressBroker | None = None) -> None:
        """Configure the fetcher, rate limiter, and egress guard.

        Args:
            config: Fully-resolved application configuration.
            limiter: Optional shared :class:`HostRateLimiter` instance.
            egress: Optional shared :class:`EgressBroker` for policy enforcement.
        """
        self.config = config
        self.limiter = limiter or HostRateLimiter(float(config.section("http").get("delay_seconds", 1)))
        self.target_policy = NetworkTargetPolicy(config)
        self.egress = egress or EgressBroker(config, policy=self.target_policy)
        self._playwright_pool: PlaywrightPool | None = None
        self._pool_lock = threading.Lock()

    def close(self) -> None:
        """Shut down the browser pool and release all worker threads."""
        with self._pool_lock:
            if self._playwright_pool is not None:
                self._playwright_pool.close()
                self._playwright_pool = None

    def __enter__(self) -> BrowserFetcher:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def fetch(self, request: CrawlRequest) -> FetchResult:
        """Render ``request`` in a browser and return the page result.

        Routes through the configured engine (Playwright or Selenium),
        executing browser actions and capturing API responses.  Egress
        policy is enforced before, during, and after navigation.

        Args:
            request: The crawl request with ``render=True``.

        Returns:
            A :class:`FetchResult` containing the rendered page content.
        """
        try:
            with self.egress.request(
                request.url,
                purpose="browser",
                headers=request.headers,
                count_request=False,
            ):
                self.limiter.wait(request.url)
                engine = str(self.config.section("browser").get("engine", "playwright")).lower()
                if engine == "playwright":
                    result = self._playwright(request)
                elif engine == "selenium":
                    result = self._selenium(request)
                else:
                    raise ValueError("browser.engine只能是playwright或selenium")
            self.egress.record_success(result.final_url)
            return result
        except PermissionError:
            raise
        except Exception as exc:
            self.egress.record_failure(request.url, error=str(exc))
            raise

    def _playwright(self, request: CrawlRequest) -> FetchResult:
        try:
            import playwright.sync_api  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("缺少Playwright，请安装 omnicrawler[browser] 并运行 playwright install chromium") from exc
        with self._pool_lock:
            if self._playwright_pool is None:
                size = effective_browser_pool(
                    self.config, int(self.config.section("browser").get("pool_size", 2))
                )
                self._playwright_pool = PlaywrightPool(
                    self.config, self.target_policy, max(1, min(size, 8)), self.egress
                )
        return self._playwright_pool.fetch(request)

    @staticmethod
    def _run_actions(page, actions) -> None:
        run_actions(actions, PlaywrightAdapter(page))

    @staticmethod
    def _action_locator(page, action):
        """locator 解析（实现已迁至 browser_engines._action_locator；此处保留入口）。"""
        return _action_locator(page, action)

    def _selenium(self, request: CrawlRequest) -> FetchResult:
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.service import Service
        except ImportError as exc:
            raise RuntimeError("缺少Selenium，请安装 omnicrawler[selenium]") from exc
        options = webdriver.ChromeOptions()
        # Request a WebDriver BiDi endpoint before the session starts; the
        # egress guard must be installed before the first navigation.
        options.enable_bidi = True
        # BiDi 需要 Chrome 显式开启 + 禁用后台定时器节流——macOS 节能会节流
        # BiDi WebSocket 事件，导致拦截事件流静默挂起（v0.9.1 macOS CI 实测，
        # Linux 无此行为故此前未暴露）。--enable-bidi 对应 ChromeDriver 129+
        # 的 BiDi 握手要求。
        options.add_argument("--enable-bidi")
        options.add_argument("--disable-background-timer-throttling")
        # macOS/CI 渲染慢时 driver.get() 等待渲染进程可能超时（'Timed out
        # receiving message from renderer'）。pageLoadStrategy=none 让 get()
        # 在 HTML 下载完成后即返回，配合下方 actions 的显式等待取内容，
        # 避免 chromedriver 新版本对慢渲染的卡死（v0.9.1 macOS CI 实测）。
        options.page_load_strategy = "none"
        # P2-2：可选持久化 Chromium profile（按 host+account 维度分配）。
        # 开关：browser.persist_profile = true（默认 false，保持旧行为）
        persist = bool(self.config.section("browser").get("persist_profile", False))
        profile_dir: Path | None = None
        if persist:
            host = (urlsplit(request.url).hostname or "").casefold()
            account = str(request.meta.get("account") or self.config.section("session").get("name", "default"))
            try:
                from .profile_registry import ProfileRegistry
            except Exception:  # noqa: BLE001
                ProfileRegistry = None  # type: ignore[assignment,misc]
            if ProfileRegistry is not None and host:
                registry = ProfileRegistry(self.config.workspace / "browser_profiles")
                profile = registry.acquire(host, account=account)
                profile_dir = profile.ensure()
                options.add_argument(f"--user-data-dir={profile_dir}")
        chrome_binary = os.environ.get("OMNICRAWL_CHROME_BINARY", "").strip()
        if chrome_binary:
            options.binary_location = chrome_binary
        if self.config.section("browser").get("headless", True):
            options.add_argument("--headless=new")
            # macOS 无头模式下 GPU 进程可能挂起渲染（selenium+Chrome 151 arm64 CI 实测
            # 'Timed out receiving message from renderer'），headless 下显式禁用 GPU 安全。
            options.add_argument("--disable-gpu")
        # B03-006：浏览器路径显式尊重 verify_tls；且拒绝 launch_args 关闭 TLS 校验
        # （把唯一的 TLS 放松点从"可审计的配置项"变成 launch_args 黑魔法是 MITM 面）。
        verify_tls = bool(self.config.section("http").get("verify_tls", True))
        for argument in self.config.section("browser").get("launch_args", []):
            arg = str(argument)
            if arg == "--ignore-certificate-errors" or "--ignore-certificate-errors=" in arg:
                raise ValueError(
                    "browser.launch_args 禁止关闭 TLS 校验（--ignore-certificate-errors）；"
                    "如需关闭请用可审计的 http.verify_tls=false"
                )
            options.add_argument(arg)
        if not verify_tls:
            options.add_argument("--ignore-certificate-errors")
            LOGGER.warning("浏览器路径 verify_tls=false：TLS 校验已关闭（仅限受控内网站点）")
        started = time.monotonic()
        driver_path = os.environ.get("OMNICRAWL_SELENIUM_DRIVER", "").strip()
        if not driver_path:
            from ...core.runtime_paths import application_dir, is_frozen

            if is_frozen():
                expected = application_dir() / "runtime" / "selenium" / "chromedriver.exe"
                if not expected.is_file():
                    # F38：标称离线自包含便携包缺内置驱动时，绝不触发 Selenium Manager 联网下载
                    raise RuntimeError(
                        f"未找到内置 ChromeDriver（{expected}）。请重新解压完整便携包，"
                        "或设置 OMNICRAWL_SELENIUM_DRIVER 指向可用驱动。"
                    )
                # F38：冻结模式只用内置驱动，绝不落到 Service() 的联网回退
                driver_path = str(expected)
        service = Service(executable_path=driver_path) if driver_path else Service()
        try:
            driver = webdriver.Chrome(service=service, options=options)
        except Exception:
            # P2-2：profile_dir 下 Chromium 可能残留 SingletonLock，
            # 回退到临时 profile（放弃持久化）保证主流程仍可运行
            if profile_dir is not None:
                # P2-2：arguments 是只读 property（getter 返回内部列表引用），
                # 原地清掉 --user-data-dir= 以放弃 profile 持久化。
                options.arguments[:] = [
                    a for a in options.arguments if not a.startswith("--user-data-dir=")
                ]
                driver = webdriver.Chrome(service=service, options=options)
            else:
                raise
        try:
            self._install_selenium_guard(driver)
            driver.set_page_load_timeout(float(self.config.section("http").get("timeout_seconds", 60)))
            # 看门狗：BiDi 拦截在个别平台上 continue_request 可能超时挂起（selenium
            # 4.47 + Chrome 151 组合问题），导航/actions 不返回。driver.quit() 也走
            # WebSocket 同样阻塞——超时必须杀 chromedriver 进程（service.stop）强制
            # 断开，主线程的 WebDriver 调用才会抛异常恢复，随后 fail-closed 报错。
            # FINAL-D2：秒数提为局部变量，超时消息与构造同源（不再硬编码 90）
            watchdog_seconds = float(self.config.section("http").get("selenium_watchdog_seconds", 90))
            watchdog = _Watchdog(
                watchdog_seconds,
                on_timeout=driver.service.stop,
            )
            with watchdog:
                # BiDi 订阅竞态：guard 注册后首导航偶发命令超时（Windows/macOS CI
                # 实测），driver 通常仍存活——同 driver 重试一次通常可过。
                try:
                    driver.get(request.url)
                except Exception:
                    time.sleep(1.0)
                    driver.get(request.url)
                self._run_selenium_actions(driver, self.config.section("browser").get("actions", []))
                body = driver.page_source.encode("utf-8")
                final_url = driver.current_url
            if watchdog.fired:
                # FINAL-D2：秒数取自实际配置，不再硬编码 90（与 :561 构造同源）
                raise RuntimeError(
                    f"Selenium 操作超过看门狗 {watchdog_seconds:.0f}s 未完成（可能 BiDi 拦截事件流挂起）"
                )
            self.egress.authorize(final_url, purpose="browser", count_request=False)
        finally:
            driver.quit()
        maximum = int(self.config.section("http").get("max_response_bytes", 50_000_000))
        if len(body) > maximum:
            raise ResponseTooLargeError(f"浏览器页面超过大小限制: {len(body)} > {maximum}")
        self.egress.record_response(len(body), url=final_url)
        return FetchResult(request, final_url, 200, {"content-type": "text/html; charset=utf-8"}, body, time.monotonic() - started)

    def _install_selenium_guard(self, driver: Any) -> None:
        """Use WebDriver BiDi interception so Selenium subrequests cannot bypass policy."""

        egress_config = self.config.section("egress")
        if egress_config.get("allow_unintercepted_selenium", False):
            # P9-B2（B03-007/008）：opt-out 已废弃并忽略——Selenium 子请求强制
            # BiDi 拦截（fail-closed），配置存在时显式告警而非静默放行。
            LOGGER.warning(
                "egress.allow_unintercepted_selenium=true 已废弃并忽略："
                "Selenium 子请求强制经过 BiDi 拦截与出口策略，无法绕过"
            )
        # S2.5.12：默认启用 BiDi 拦截；experimental 显式关闭时 fail-closed 提示
        if not egress_config.get("experimental_selenium_bidi_guard", True):
            raise RuntimeError(
                "Selenium逐请求拦截已显式关闭（egress.experimental_selenium_bidi_guard=false），"
                "子请求将绕过网络策略；请改用Playwright"
            )
        try:
            network = driver.network

            def guard(request: Any) -> None:
                try:
                    headers = getattr(request, "headers", {}) or {}
                    self.egress.authorize(
                        str(request.url),
                        purpose="browser",
                        headers=headers if isinstance(headers, dict) else {},
                    )
                except PermissionError:
                    request.fail()
                except Exception as exc:
                    # S2.5.12：非权限异常（预算/熔断/瞬态）放行请求而非挂死渲染
                    LOGGER.warning(
                        "BiDi guard 异常放行请求 %s: %s: %s",
                        request.url, type(exc).__name__, exc,
                    )
                    request.continue_request()
                else:
                    try:
                        request.continue_request()
                    except Exception as exc:
                        # macOS 上 selenium BiDi continue_request 等待命令响应可能超时
                        # （WebDriverException: Timed out waiting for response to BiDi
                        # command，selenium 4.47 + Chrome 151 已知组合问题）。异常发生在
                        # selenium 回调线程，不逃逸到主流程——记录并尝试 fail，避免请求
                        # 永久挂起导致 driver.get() 卡死。
                        LOGGER.error(
                            "BiDi continue_request 失败（%s）: %s，尝试 fail_request",
                            request.url, exc,
                        )
                        try:
                            request.fail()
                        except Exception as fail_exc:
                            LOGGER.error("BiDi fail_request 也失败: %s", fail_exc)

            network.add_request_handler("before_request", guard)
            # BiDi 网络订阅广播与首个导航请求存在竞态：guard 刚注册完浏览器
            # 事件流尚未完全稳定，首请求立即拦截时 continue_request 命令可能
            # 超时（'Timed out waiting for response to BiDi command'，selenium
            # 4.47 + Chrome 151，Windows/macOS CI 实测）。给事件流短暂稳定期，
            # 显著降低首请求命中竞态的概率。
            time.sleep(0.5)
        except Exception as exc:
            raise RuntimeError(
                "Selenium BiDi 逐请求拦截不可用；请改用Playwright"
            ) from exc

    @staticmethod
    def _run_selenium_actions(driver: Any, actions: list[dict[str, Any]]) -> None:
        """Execute the portable browser action contract with Selenium."""
        run_actions(actions, SeleniumAdapter(driver))


def register(registry) -> None:
    registry.register_fetcher("browser", BrowserFetcher)
