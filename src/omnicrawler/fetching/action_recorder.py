from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RecordedAction:
    action: str
    selector: str = ""
    value: str = ""
    key: str = ""
    timeout_ms: int = 0

    def config(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value not in {"", 0}}


class ActionSequence:
    """Normalize browser events into the small action language used by BrowserFetcher."""

    def __init__(self) -> None:
        self.actions: list[RecordedAction] = []
        self.events_received = 0

    def add_event(self, event: dict[str, Any]) -> None:
        self.events_received += 1
        kind = str(event.get("type", "")).casefold()
        selector = str(event.get("selector", "")).strip()
        if kind == "click" and selector:
            action = RecordedAction("click", selector=selector)
        elif kind in {"change", "fill", "input"} and selector:
            value = str(event.get("value", ""))
            if bool(event.get("secret")):
                value = "secret://browser_password"
            action = RecordedAction("fill", selector=selector, value=value)
            if self.actions and self.actions[-1].action == "fill" and self.actions[-1].selector == selector:
                self.actions[-1] = action
                return
        elif kind in {"keydown", "press"} and selector and event.get("key") in {"Enter", "Tab", "Escape"}:
            action = RecordedAction("press", selector=selector, key=str(event["key"]))
        elif kind == "scroll":
            action = RecordedAction("scroll_bottom")
        elif kind == "wait":
            action = RecordedAction("wait_ms", value=str(max(0, int(event.get("value", 1000)))))
        else:
            return
        if self.actions and action == self.actions[-1]:
            return
        self.actions.append(action)

    def to_config(self) -> list[dict[str, Any]]:
        return [action.config() for action in self.actions]

    def delete(self, index: int) -> RecordedAction:
        """Delete one recorded action while keeping the rest of the sequence stable."""
        return self.actions.pop(index)

    def move(self, source: int, destination: int) -> None:
        if not 0 <= destination < len(self.actions):
            raise IndexError(destination)
        action = self.actions.pop(source)
        self.actions.insert(destination, action)

    def replace(self, index: int, event: dict[str, Any]) -> None:
        """Re-record one action from a normalised browser event."""
        replacement = ActionSequence()
        replacement.add_event(event)
        if len(replacement.actions) != 1:
            raise ValueError("事件不能转换为单个可执行动作")
        self.actions[index] = replacement.actions[0]

    @property
    def sensitive_steps(self) -> tuple[int, ...]:
        return tuple(index for index, action in enumerate(self.actions) if action.value.startswith("secret://"))

    def save(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            yaml.safe_dump({"browser": {"actions": self.to_config()}}, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        return path


@dataclass(frozen=True, slots=True)
class ApiCandidate:
    url: str
    method: str = "GET"
    sample_status: int = 0
    schema_valid: bool = False
    within_scope: bool = False

    @property
    def may_suggest_rest(self) -> bool:
        """REST is only a suggestion after a successful, scoped sample validation."""
        return self.method.upper() in {"GET", "POST"} and self.sample_status == 200 and self.schema_valid and self.within_scope

_RECORDER_SCRIPT = r"""
(() => {
  if (window.__omnicrawlerRecorderInstalled) return;
  window.__omnicrawlerRecorderInstalled = true;
  const esc = value => CSS.escape(String(value));
  function selector(el) {
    if (!el || el.nodeType !== 1) return '';
    if (el.id) return '#' + esc(el.id);
    for (const name of ['data-testid','data-test','itemprop','name','aria-label']) {
      const value = el.getAttribute(name);
      if (value) return el.tagName.toLowerCase() + '[' + name + '="' + value.replace(/"/g, '\\"') + '"]';
    }
    const classes = [...el.classList].filter(v => v.length < 80 && !/[a-f0-9]{10,}/i.test(v)).slice(0,2);
    if (classes.length) return el.tagName.toLowerCase() + classes.map(v => '.' + esc(v)).join('');
    const parts = [];
    let current = el;
    while (current && current.nodeType === 1 && parts.length < 4) {
      let part = current.tagName.toLowerCase();
      const siblings = current.parentElement ? [...current.parentElement.children].filter(x => x.tagName === current.tagName) : [];
      if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(current) + 1) + ')';
      parts.unshift(part); current = current.parentElement;
    }
    return parts.join(' > ');
  }
  document.addEventListener('click', e => window.__omnicrawlerEmit({type:'click', selector:selector(e.target)}), true);
  document.addEventListener('change', e => window.__omnicrawlerEmit({
    type:'change', selector:selector(e.target), value:e.target.value || '',
    secret:e.target.type === 'password'
  }), true);
  document.addEventListener('keydown', e => {
    if (['Enter','Tab','Escape'].includes(e.key)) window.__omnicrawlerEmit({type:'keydown', selector:selector(e.target), key:e.key});
  }, true);
  let lastScroll = 0;
  window.addEventListener('scroll', () => {
    if (Date.now() - lastScroll > 1000 && window.scrollY + window.innerHeight >= document.body.scrollHeight - 50) {
      lastScroll = Date.now(); window.__omnicrawlerEmit({type:'scroll'});
    }
  }, true);
})();
"""


def record_with_playwright(url: str, output: Path, *, timeout_seconds: int = 300) -> dict[str, Any]:
    """Open a visible browser and record normal user interactions until the window closes."""

    from ..core.config import DEFAULTS, AppConfig
    from ..security.policy import NetworkTargetPolicy

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError("Recording requires Playwright and Chromium") from exc
    root = output.parent.resolve()
    raw = json.loads(json.dumps(DEFAULTS))
    raw["project"] = {"name": "action_recorder", "workspace": str(root / ".recorder")}
    raw["source"] = {"kind": "browser", "seeds": [url]}
    config = AppConfig(root / ".recorder.yaml", root, raw, root / ".recorder")
    policy = NetworkTargetPolicy(config)
    policy.require(url)
    sequence = ActionSequence()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.expose_function("__omnicrawlerEmit", sequence.add_event)
        page.add_init_script(_RECORDER_SCRIPT)
        page.route(
            "**/*",
            # B03-016：policy.allowed 是域名层门禁（主机级别拦截）；
            # 子资源 URL 的精细授权由上层 egress broker 在传输层执行。
            lambda route: route.continue_() if policy.allowed(route.request.url)[0] else route.abort(),
        )
        page.goto(url, wait_until="domcontentloaded")
        started = time.monotonic()
        while not page.is_closed() and time.monotonic() - started < timeout_seconds:
            try:
                page.wait_for_timeout(250)
            except Exception:
                break
        screenshot = output.with_suffix(".png")
        if not page.is_closed():
            try:
                page.screenshot(path=str(screenshot), full_page=True)
            except Exception:
                screenshot = Path()
        try:
            context.close()
            browser.close()
        except Exception:
            logger.debug("Failed to close browser context/browser", exc_info=True)
    sequence.save(output)
    diagnostics = output.with_suffix(".trace.json")
    result = {
        "created": str(output),
        "actions": sequence.to_config(),
        "events_received": sequence.events_received,
        "timed_out": time.monotonic() - started >= timeout_seconds,
        "final_screenshot": str(screenshot) if screenshot and screenshot.is_file() else "",
    }
    diagnostics.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    result["diagnostics"] = str(diagnostics)
    return result
