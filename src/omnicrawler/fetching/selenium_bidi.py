"""BiDi reads keep intercepted navigation free of classic command waits."""
from __future__ import annotations

import json
import time
from fnmatch import fnmatch

from .browser_engines import BrowserAction, SeleniumAdapter


class _NavigationTransitionError(RuntimeError):
    """The old JavaScript context disappeared while navigation was committing."""


def _is_transition(exc: Exception) -> bool:
    from selenium.common.exceptions import WebDriverException

    return isinstance(exc, WebDriverException) and "Cannot find context with specified id" in str(exc)


class SeleniumBiDiAdapter(SeleniumAdapter):
    """Use BiDi for reads/waits and native WebDriver element interactions."""

    def __init__(self, driver, context: str) -> None:
        super().__init__(driver)
        self.context = context

    def locate(self, action: BrowserAction):
        from selenium.webdriver.remote.webelement import WebElement

        locators = []
        if action.role:
            value = {"role": action.role}
            if action.role_name is not None:
                value["name"] = action.role_name
            locators.append({"type": "accessibility", "value": value})
        locators.extend({"type": "css", "value": selector} for selector in self._choices(action)
                        if not action.role or selector != f'[role="{action.role}"]')
        for locator in locators:
            try:
                nodes = self._driver.browsing_context.locate_nodes(
                    context=self.context, locator=locator, max_node_count=1,
                )
            except Exception as exc:
                if _is_transition(exc):
                    return None
                raise
            if nodes:
                shared_id = nodes[0].get("sharedId") if isinstance(nodes[0], dict) else None
                if not isinstance(shared_id, str) or not shared_id:
                    raise RuntimeError("BiDi locator did not return a shared element reference")
                return WebElement(self._driver, shared_id)
        return None

    def _read_string(self, expression: str) -> str:
        try:
            result = self._driver.script.evaluate(
                expression=expression, target={"context": self.context}, await_promise=False,
            )
        except Exception as exc:
            if _is_transition(exc):
                raise _NavigationTransitionError from exc
            raise
        value = result.get("result", {}) if isinstance(result, dict) else {}
        if (not isinstance(result, dict) or result.get("type") != "success" or not isinstance(value, dict)
                or value.get("type") != "string" or not isinstance(value.get("value"), str)):
            raise RuntimeError("BiDi document read did not return a string")
        return value["value"]

    def wait_for_url(self, action: BrowserAction) -> None:
        pattern = str(action.value or "**/*").replace("**", "*")
        self._WebDriverWait(self._driver, max(0.1, action.timeout_ms / 1000),
                            ignored_exceptions=(_NavigationTransitionError,)).until(
            lambda _: fnmatch(self._read_string("location.href"), pattern),
        )

    def scroll_bottom(self, action: BrowserAction) -> None:
        for _ in range(action.times):
            self._driver.script.evaluate(
                expression="window.scrollTo(0, document.body.scrollHeight)",
                target={"context": self.context}, await_promise=False,
            )
            if action.pause_ms:
                time.sleep(action.pause_ms / 1000)

    def read_document(self, timeout: float = 60) -> tuple[str, bytes]:
        def snapshot(_driver):
            values = json.loads(self._read_string(
                "JSON.stringify([location.href, document.documentElement.outerHTML, document.readyState])",
            ))
            if not isinstance(values, list) or len(values) != 3 or not all(isinstance(value, str) for value in values):
                raise RuntimeError("BiDi document read returned invalid URL/content")
            return values[:2] if values[2] == "complete" else False

        values = self._WebDriverWait(self._driver, max(0.1, timeout),
                                    ignored_exceptions=(_NavigationTransitionError,)).until(snapshot)
        return values[0], values[1].encode("utf-8")
