"""浏览器引擎抽象与动作派发 —— 从 browser_fetcher.py 迁出（P1-3 第一批）。

含：
- ``BrowserAction``：动作数据类（from_dict 解析）
- ``BrowserEngine``：引擎协议（runtime_checkable Protocol）
- ``PlaywrightAdapter`` / ``SeleniumAdapter``：两个引擎实现
- ``_dispatch_action`` / ``run_actions``：动作分派与批量执行

无包内反向依赖（仅 stdlib + 本模块内互相引用），可独立单测。
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol, runtime_checkable


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

def _action_locator(page: Any, action: dict[str, Any]):
    """按 role → selectors → selector 顺序解析 locator（无匹配返回单选择器或 None）。

    实现自 BrowserFetcher._action_locator 迁出；宿主保留同名静态委托以兼容外部调用点。
    """
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

class PlaywrightAdapter:
    """Adapt a Playwright ``page`` object to the :class:`BrowserEngine` protocol."""

    def __init__(self, page: Any) -> None:
        self._page = page

    def _locator(self, action: BrowserAction):
        return _action_locator(self._page, asdict(action))

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
        case "scroll":
            # EasySpider 导入的 scroll 动作等价 scroll_bottom（times=value）
            count = max(1, int(action.value or 1))
            engine.scroll_bottom(
                BrowserAction(name="scroll_bottom", times=count, pause_ms=action.pause_ms)
            )
        case "wait_ms":
            engine.wait_ms(action)
        case "manual_pause":
            # manual_pause is treated as a long wait_ms
            engine.wait_ms(BrowserAction(name="wait_ms", value=str(action.timeout_ms or 30_000)))
        case _:
            raise ValueError(f"不支持的浏览器动作: {action.name}")

def run_actions_for_page(page: Any, actions: list[dict[str, Any]]) -> None:
    """对 Playwright page 执行动作序列（run_actions + PlaywrightAdapter 的组合）。

    自 BrowserFetcher._run_actions 迁出实现；宿主保留同名静态委托以兼容测试调用点。
    """
    run_actions(actions, PlaywrightAdapter(page))

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
