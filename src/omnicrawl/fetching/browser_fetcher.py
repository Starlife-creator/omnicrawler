from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

from typing import Protocol, runtime_checkable

from ..core.config import AppConfig
from ..core.errors import EgressBudgetExceededError, ResponseTooLargeError
from ..core.models import CrawlRequest, FetchResult
from ..runtime.resource_profiles import effective_browser_pool
from ..security.egress import EgressBroker
from ..security.policy import HostRateLimiter, NetworkTargetPolicy

# ---------------------------------------------------------------------------
# Unified browser action protocol
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class BrowserAction:
    """Single browser interaction step."""

    name: str
    selector: str | None = None
    selectors: list[str] | None = None
    role: str | None = None
    role_name: str | None = None
    value: str | None = None
    key: str | None = None
    optional: bool = False
    if_present: bool = False
    timeout_ms: int = 10_000
    times: int = 1
    pause_ms: int = 750

    @classmethod
    def from_dict(cls, raw: dict) -> BrowserAction:
        """Construct a ``BrowserAction`` from a raw config dict."""
        return cls(
            name=str(raw.get("action", "")),
            selector=raw.get("selector"),
            selectors=raw.get("selectors"),
            role=raw.get("role"),
            role_name=raw.get("name"),
            value=str(raw["value"]) if "value" in raw else None,
            key=raw.get("key"),
            optional=bool(raw.get("optional", False)),
            if_present=bool(raw.get("if_present", False)),
            timeout_ms=int(raw.get("timeout_ms", 10_000)),
            times=max(1, int(raw.get("times", 1))),
            pause_ms=max(0, int(raw.get("pause_ms", 750))),
        )


@runtime_checkable
class BrowserEngine(Protocol):
    """Minimal interface that both Playwright and Selenium adapters satisfy."""

    def locate(self, action: BrowserAction):
        """Try to find an element for the action, returning ``None`` if absent."""
        ...
    def wait_for(self, action: BrowserAction) -> None:
        """Wait until the action's target element appears in the DOM."""
        ...
    def wait_for_url(self, action: BrowserAction) -> None:
        """Wait until the page URL matches the action's glob pattern."""
        ...
    def click(self, action: BrowserAction) -> None:
        """Click the element identified by the action."""
        ...
    def fill(self, action: BrowserAction) -> None:
        """Type ``action.value`` into the identified input element."""
        ...
    def press(self, action: BrowserAction) -> None:
        """Press ``action.key`` on the identified element."""
        ...
    def select_option(self, action: BrowserAction) -> None:
        """Select ``action.value`` in a ``<select>`` element."""
        ...
    def check(self, action: BrowserAction) -> None:
        """Ensure the identified checkbox is checked."""
        ...
    def scroll_bottom(self, action: BrowserAction) -> None:
        """Scroll to the bottom of the page, ``action.times`` times."""
        ...
    def wait_ms(self, action: BrowserAction) -> None:
        """Pause execution for ``action.value`` milliseconds."""
        ...


class PlaywrightAdapter:
    """Adapt a Playwright ``page`` object to the :class:`BrowserEngine` protocol."""

    def __init__(self, page: Any) -> None:
        self._page = page

    def _locator(self, action: BrowserAction):
        return BrowserFetcher._action_locator(self._page, asdict(action))

    def locate(self, action: BrowserAction):
        if action.selector or action.selectors or action.role:
            loc = self._locator(action)
            if loc is not None and loc.count() == 0:
                return None
            return loc
        return None

    def wait_for(self, action: BrowserAction) -> None:
        loc = self._locator(action)
        if loc is None:
            raise ValueError("wait_for requires selector or role")
        loc.wait_for(timeout=action.timeout_ms)

    def wait_for_url(self, action: BrowserAction) -> None:
        self._page.wait_for_url(str(action.value or "**/*"), timeout=action.timeout_ms)

    def click(self, action: BrowserAction) -> None:
        loc = self._locator(action)
        if loc is None:
            raise ValueError("click requires selector or role")
        loc.click(timeout=action.timeout_ms)

    def fill(self, action: BrowserAction) -> None:
        loc = self._locator(action)
        if loc is None:
            raise ValueError("fill requires selector or role")
        loc.fill(str(action.value or ""))

    def press(self, action: BrowserAction) -> None:
        loc = self._locator(action)
        if loc is None:
            raise ValueError("press requires selector or role")
        loc.press(str(action.key or "Enter"))

    def select_option(self, action: BrowserAction) -> None:
        loc = self._locator(action)
        if loc is None:
            raise ValueError("select_option requires selector or role")
        loc.select_option(str(action.value or ""))

    def check(self, action: BrowserAction) -> None:
        loc = self._locator(action)
        if loc is None:
            raise ValueError("check requires selector or role")
        loc.check()

    def scroll_bottom(self, action: BrowserAction) -> None:
        for _ in range(action.times):
            self._page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            if action.pause_ms:
                self._page.wait_for_timeout(action.pause_ms)

    def wait_ms(self, action: BrowserAction) -> None:
        self._page.wait_for_timeout(int(action.value or 1000))


class SeleniumAdapter:
    """Adapt a Selenium ``driver`` object to the :class:`BrowserEngine` protocol."""

    def __init__(self, driver: Any) -> None:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait

        self._driver = driver
        self._By = By
        self._WebDriverWait = WebDriverWait

    def _choices(self, action: BrowserAction) -> list[str]:
        result: list[str] = []
        if action.selectors:
            result = [str(s) for s in action.selectors]
        if action.selector:
            result.insert(0, str(action.selector))
        if action.role:
            result.insert(0, f'[role="{action.role}"]')
        return list(dict.fromkeys(result))

    def locate(self, action: BrowserAction):
        expected_name = str(action.role or "").strip()
        for selector in self._choices(action):
            for element in self._driver.find_elements(self._By.CSS_SELECTOR, selector):
                if not expected_name or element.accessible_name == expected_name or element.text == expected_name:
                    return element
        return None

    def _ensure(self, action: BrowserAction):
        timeout = max(0.1, action.timeout_ms / 1000)
        return self._WebDriverWait(self._driver, timeout).until(lambda _: self.locate(action))

    def wait_for(self, action: BrowserAction) -> None:
        timeout = max(0.1, action.timeout_ms / 1000)
        self._WebDriverWait(self._driver, timeout).until(lambda _: self.locate(action))

    def wait_for_url(self, action: BrowserAction) -> None:
        from fnmatch import fnmatch

        timeout = max(0.1, action.timeout_ms / 1000)
        pattern = str(action.value or "**/*").replace("**", "*")
        self._WebDriverWait(self._driver, timeout).until(lambda _: fnmatch(self._driver.current_url, pattern))

    def click(self, action: BrowserAction) -> None:
        self._ensure(action).click()

    def fill(self, action: BrowserAction) -> None:
        element = self._ensure(action)
        element.clear()
        element.send_keys(str(action.value or ""))

    def press(self, action: BrowserAction) -> None:
        from selenium.webdriver.common.keys import Keys

        key = str(action.key or "Enter")
        self._ensure(action).send_keys(getattr(Keys, key.upper(), key))

    def select_option(self, action: BrowserAction) -> None:
        from selenium.webdriver.support.ui import Select

        Select(self._ensure(action)).select_by_value(str(action.value or ""))

    def check(self, action: BrowserAction) -> None:
        element = self._ensure(action)
        if not element.is_selected():
            element.click()

    def scroll_bottom(self, action: BrowserAction) -> None:
        for _ in range(action.times):
            self._driver.execute_script("window.scrollTo(0, document.body.scrollHeight)")
            if action.pause_ms:
                time.sleep(action.pause_ms / 1000)

    def wait_ms(self, action: BrowserAction) -> None:
        time.sleep(max(0, int(action.value or 1000)) / 1000)


def _dispatch_action(action: BrowserAction, engine: BrowserEngine) -> None:
    """Execute a single :class:`BrowserAction` against the given *engine*."""
    match action.name:
        case "wait_for":
            engine.wait_for(action)
        case "wait_for_url":
            engine.wait_for_url(action)
        case "click":
            engine.click(action)
        case "fill":
            engine.fill(action)
        case "press":
            engine.press(action)
        case "select_option":
            engine.select_option(action)
        case "check":
            engine.check(action)
        case "scroll_bottom":
            engine.scroll_bottom(action)
        case "wait_ms":
            engine.wait_ms(action)
        case "manual_pause":
            # manual_pause is treated as a long wait_ms
            engine.wait_ms(BrowserAction(name="wait_ms", value=str(action.timeout_ms or 30_000)))
        case _:
            raise ValueError(f"不支持的浏览器动作: {action.name}")


def run_actions(actions: list[dict], engine: BrowserEngine) -> None:
    """Iterate over raw action dicts, convert to :class:`BrowserAction`, and dispatch."""
    for index, raw in enumerate(actions, 1):
        action = BrowserAction.from_dict(raw)
        if action.if_present and engine.locate(action) is None:
            continue
        try:
            _dispatch_action(action, engine)
        except Exception as exc:
            if action.optional:
                continue
            raise RuntimeError(f"浏览器动作第 {index} 步失败 ({action.name}): {exc}") from exc


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
            raise RuntimeError("缺少Playwright，请安装 omnicrawl[browser] 并运行 playwright install chromium") from exc
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
        raw_role = action.get("role")
        role = str(raw_role).strip() if raw_role else ""
        if role:
            return page.get_by_role(role, name=action.get("role_name"))
        selectors = action.get("selectors")
        choices = [str(item) for item in selectors] if isinstance(selectors, list) else []
        if action.get("selector"):
            choices.insert(0, str(action["selector"]))
        for selector in dict.fromkeys(choices):
            locator = page.locator(selector)
            if locator.count() > 0:
                return locator
        return page.locator(choices[0]) if choices else None

    def _selenium(self, request: CrawlRequest) -> FetchResult:
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.service import Service
        except ImportError as exc:
            raise RuntimeError("缺少Selenium，请安装 omnicrawl[selenium]") from exc
        options = webdriver.ChromeOptions()
        # Request a WebDriver BiDi endpoint before the session starts; the
        # egress guard must be installed before the first navigation.
        options.enable_bidi = True
        chrome_binary = os.environ.get("OMNICRAWL_CHROME_BINARY", "").strip()
        if chrome_binary:
            options.binary_location = chrome_binary
        if self.config.section("browser").get("headless", True):
            options.add_argument("--headless=new")
        for argument in self.config.section("browser").get("launch_args", []):
            options.add_argument(str(argument))
        started = time.monotonic()
        driver_path = os.environ.get("OMNICRAWL_SELENIUM_DRIVER", "").strip()
        service = Service(executable_path=driver_path) if driver_path else Service()
        driver = webdriver.Chrome(service=service, options=options)
        try:
            self._install_selenium_guard(driver)
            driver.set_page_load_timeout(float(self.config.section("http").get("timeout_seconds", 25)))
            driver.get(request.url)
            self._run_selenium_actions(driver, self.config.section("browser").get("actions", []))
            body = driver.page_source.encode("utf-8")
            final_url = driver.current_url
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
            return
        if not egress_config.get("experimental_selenium_bidi_guard", False):
            raise RuntimeError(
                "Selenium逐请求拦截在当前版本中属于实验能力，默认安全关闭；"
                "请改用Playwright，或显式接受风险并设置"
                "egress.allow_unintercepted_selenium=true"
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
                else:
                    request.continue_request()

            network.add_request_handler("before_request", guard)
        except Exception as exc:
            raise RuntimeError(
                "Selenium逐请求安全拦截不可用；请改用Playwright，或显式设置"
                "egress.allow_unintercepted_selenium=true"
            ) from exc

    @staticmethod
    def _run_selenium_actions(driver: Any, actions: list[dict[str, Any]]) -> None:
        """Execute the portable browser action contract with Selenium."""
        run_actions(actions, SeleniumAdapter(driver))


@dataclass(slots=True)
class _PoolTask:
    request: CrawlRequest
    done: threading.Event
    result: FetchResult | None = None
    error: BaseException | None = None


class PlaywrightPool:
    """Thread-safe browser pool; each worker owns one browser and isolated reusable contexts."""

    def __init__(
        self,
        config: AppConfig,
        target_policy: NetworkTargetPolicy,
        size: int,
        egress: EgressBroker | None = None,
    ) -> None:
        self.config = config
        self.target_policy = target_policy
        self.egress = egress or EgressBroker(config, policy=target_policy)
        self._queues: list[queue.Queue[_PoolTask | None]] = [queue.Queue() for _ in range(size)]
        self._threads: list[threading.Thread] = []
        self._counter = 0
        self._lock = threading.Lock()
        self._closed = False
        for index, work_queue in enumerate(self._queues):
            thread = threading.Thread(
                target=self._worker,
                args=(work_queue,),
                name=f"omnicrawl-browser-{index}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def fetch(self, request: CrawlRequest) -> FetchResult:
        task = _PoolTask(request, threading.Event())
        with self._lock:
            if self._closed:
                raise RuntimeError("Browser pool is closed")
            work_queue = self._queues[self._counter % len(self._queues)]
            self._counter += 1
            work_queue.put(task)  # 锁内入队，防止 close() 插入 None 哨兵
        timeout = float(self.config.section("http").get("timeout_seconds", 25)) + 30
        if not task.done.wait(timeout):
            raise TimeoutError(f"Browser pool worker did not finish within {timeout:g} seconds")
        if task.error is not None:
            raise task.error
        if task.result is None:
            raise RuntimeError("Browser pool returned no result")
        return task.result

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        for work_queue in self._queues:
            work_queue.put(None)
        for thread in self._threads:
            thread.join(timeout=10)

    def _worker(self, work_queue: queue.Queue) -> None:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                browser_config = self.config.section("browser")
                browser = playwright.chromium.launch(
                    headless=bool(browser_config.get("headless", True)),
                    args=[str(item) for item in browser_config.get("launch_args", [])],
                )
                contexts: dict[str, Any] = {}
                try:
                    while True:
                        task = work_queue.get()
                        if task is None:
                            return
                        try:
                            task.result = self._render(browser, contexts, task.request)
                        except BaseException as exc:
                            task.error = exc
                        finally:
                            task.done.set()
                finally:
                    for context in contexts.values():
                        try:
                            context.close()
                        except Exception as exc:
                            LOGGER.debug("Browser cleanup error: %s", exc)
                    browser.close()
        except Exception as startup_error:
            while True:
                task = work_queue.get()
                if task is None:
                    return
                task.error = startup_error
                task.done.set()

    def _render(self, browser: Any, contexts: dict[str, Any], request: CrawlRequest) -> FetchResult:
        context_key = self._context_key(request)
        for attempt in range(2):
            context = contexts.get(context_key)
            if context is None:
                context = contexts[context_key] = self._new_context(browser, context_key, request)
            page = context.new_page()
            api_candidates: list[dict[str, Any]] = []
            try:
                page.route("**/*", self._guard_route)
                page.on("response", lambda response, ac=api_candidates: self._capture_response(response, ac))
                started = time.monotonic()
                browser_config = self.config.section("browser")
                response = page.goto(
                    request.url,
                    wait_until=str(browser_config.get("wait_until", "networkidle")),
                    timeout=int(float(self.config.section("http").get("timeout_seconds", 25)) * 1000),
                )
                BrowserFetcher._run_actions(page, browser_config.get("actions", []))
                body = page.content().encode("utf-8")
                final_url = page.url
                self.egress.authorize(final_url, purpose="browser", count_request=False)
                maximum = int(self.config.section("http").get("max_response_bytes", 50_000_000))
                if len(body) > maximum:
                    raise ResponseTooLargeError(f"浏览器页面超过大小限制: {len(body)} > {maximum}")
                self.egress.record_response(len(body), url=final_url)
                self._save_context(context, context_key)
                headers = {
                    "content-type": "text/html; charset=utf-8",
                    "x-omnicrawl-api-candidates": json.dumps(
                        [
                            {key: value for key, value in item.items() if key not in {"json", "text"}}
                            for item in api_candidates[:100]
                        ],
                        ensure_ascii=False,
                    ),
                }
                return FetchResult(
                    request,
                    final_url,
                    response.status if response else 200,
                    headers,
                    body,
                    time.monotonic() - started,
                    {"api_responses": api_candidates},
                )
            except Exception as exc:
                if attempt == 0:
                    LOGGER.warning("浏览器渲染第 1 次尝试失败: %s", exc)
                    try:
                        context.close()
                    except Exception as exc:
                        LOGGER.debug("Browser cleanup error: %s", exc)
                    contexts.pop(context_key, None)
                    continue
                raise
            finally:
                try:
                    page.close()
                except Exception as exc:
                    LOGGER.debug("Browser cleanup error: %s", exc)
        raise RuntimeError("Unreachable browser retry state")

    def _context_key(self, request: CrawlRequest) -> str:
        session = self.config.section("session")
        account = str(request.meta.get("account") or session.get("name", "default"))
        proxy = str(request.meta.get("proxy") or self.config.section("http").get("proxy", ""))
        return f"{account}|{proxy}"

    def _state_path(self, context_key: str) -> Path | None:
        if not self.config.section("session").get("persist_cookies", False):
            return None
        safe_name = "".join(char if char.isalnum() or char in "-_" else "_" for char in context_key)[:120]
        return self.config.workspace / "sessions" / f"{safe_name}.playwright.json"

    def _new_context(self, browser: Any, context_key: str, request: CrawlRequest) -> Any:
        state_path = self._state_path(context_key)
        options: dict[str, Any] = {"user_agent": self.config.section("http").get("user_agent")}
        if state_path and state_path.is_file():
            options["storage_state"] = str(state_path)
        proxy = str(request.meta.get("proxy") or "")
        if proxy:
            options["proxy"] = {"server": proxy}
        context = browser.new_context(**options)
        # -- 反检测增强：注入 stealth.min.js + 隐藏 webdriver 标记 --
        stealth_path = Path(__file__).resolve().parent / "stealth.min.js"
        if stealth_path.is_file():
            try:
                context.add_init_script(path=str(stealth_path))
            except Exception as exc:
                LOGGER.warning("Stealth script injection failed: %s", exc)
        try:
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
        except Exception as exc:
            LOGGER.warning("Stealth script injection failed: %s", exc)
        extra_headers = {
            **self.config.section("http").get("headers", {}),
            **self.config.section("source").get("headers", {}),
            **request.headers,
        }
        if extra_headers:
            context.set_extra_http_headers({str(key): str(value) for key, value in extra_headers.items()})
        return context

    def _save_context(self, context: Any, context_key: str) -> None:
        state_path = self._state_path(context_key)
        if state_path is None:
            return
        state_path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(state_path))
        try:
            os.chmod(state_path, 0o600)
        except OSError:
            pass

    def _guard_route(self, route: Any) -> None:
        try:
            headers = getattr(route.request, "headers", {}) or {}
            egress = getattr(self, "egress", None)
            if egress is None:
                self.target_policy.require(route.request.url)
            else:
                egress.authorize(
                    route.request.url,
                    purpose="browser",
                    headers=headers if isinstance(headers, dict) else {},
                )
        except PermissionError:
            route.abort("blockedbyclient")
        else:
            route.continue_()

    def _capture_response(self, response: Any, output: list[dict[str, Any]]) -> None:
        try:
            resource_type = response.request.resource_type
            content_type = response.headers.get("content-type", "")
            if resource_type in {"xhr", "fetch"} or "json" in content_type:
                entry: dict[str, Any] = {
                    "url": response.url,
                    "method": response.request.method,
                    "status": response.status,
                    "resource_type": resource_type,
                    "content_type": content_type,
                }
                request_headers = getattr(response.request, "headers", {}) or {}
                if isinstance(request_headers, dict):
                    safe_names = {"accept", "content-type", "x-requested-with", "origin", "referer"}
                    entry["request_headers"] = {
                        str(key): str(value) for key, value in request_headers.items()
                        if str(key).casefold() in safe_names
                    }
                if response.request.method.upper() not in {"GET", "HEAD"}:
                    try:
                        post_data = response.request.post_data
                        if post_data:
                            try:
                                entry["request_payload"] = json.loads(post_data)
                            except (TypeError, json.JSONDecodeError):
                                entry["request_payload"] = post_data
                    except Exception as exc:
                        LOGGER.info("Evidence capture failed, skipping: %s", exc)
                browser = self.config.section("browser")
                if browser.get("capture_api_responses", True):
                    per_response = max(0, int(browser.get("max_api_response_bytes", 1_000_000)))
                    total_limit = max(0, int(browser.get("max_api_capture_bytes", 10_000_000)))
                    captured = sum(int(item.get("captured_bytes", 0)) for item in output)
                    body = response.body()
                    if len(body) <= per_response and captured + len(body) <= total_limit:
                        egress = getattr(self, "egress", None)
                        if egress is not None:
                            egress.record_response(len(body), url=response.url)
                        entry["captured_bytes"] = len(body)
                        text = body.decode("utf-8", errors="replace")
                        if "json" in content_type:
                            try:
                                entry["json"] = json.loads(text)
                            except json.JSONDecodeError:
                                entry["text"] = text
                        else:
                            entry["text"] = text
                    else:
                        entry["capture_skipped"] = "size_limit"
                output.append(entry)
        except EgressBudgetExceededError:
            egress = getattr(self, "egress", None)
            if egress is not None:
                egress.disconnect_task()
        except Exception as exc:
            LOGGER.info("Evidence capture failed, skipping: %s", exc)


def register(registry) -> None:
    registry.register_fetcher("browser", BrowserFetcher)
