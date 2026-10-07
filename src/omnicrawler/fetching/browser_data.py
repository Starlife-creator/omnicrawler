"""Bounded data readiness and virtual-list collection, shared by browser engines."""
from __future__ import annotations

import json
import time
from collections.abc import Callable
from fnmatch import fnmatch
from typing import Any

Reader = Callable[[str], Any]


def _integer(config: dict[str, Any], name: str, default: int, minimum: int, maximum: int) -> int:
    value = config.get(name, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"browser data {name} must be between {minimum} and {maximum}")
    return value


def _snapshot_expression(config: dict[str, Any]) -> str:
    return "(() => { const cfg=" + json.dumps(config) + ";" + """
      const visible=e=>!!e && e.getClientRects().length>0 && getComputedStyle(e).visibility!=='hidden';
      const items=[...document.querySelectorAll(cfg.item_selector || cfg.selector)];
      return {items:items.map(e=>({id:cfg.identity_attribute?e.getAttribute(cfg.identity_attribute):e.textContent,
                                  html:e.outerHTML,text:e.textContent})),
              loading:cfg.hidden_selector?visible(document.querySelector(cfg.hidden_selector)):false,
              ended:cfg.end_selector?visible(document.querySelector(cfg.end_selector)):false};
    })()"""


def wait_for_data(read: Reader, config: dict[str, Any], *, responses: list[dict[str, Any]] | None = None,
                  should_stop: Callable[[], bool] | None = None) -> dict[str, Any]:
    if not config:
        return {"state": "undeclared", "completeness": "unknown"}
    if not isinstance(config.get("selector"), str) or not config["selector"].strip():
        raise ValueError("browser readiness requires a selector")
    timeout = _integer(config, "timeout_ms", 10000, 1, 120000) / 1000
    stable = _integer(config, "stable_ms", 250, 0, 10000) / 1000
    minimum = _integer(config, "min_count", 1, 0, 25000)
    deadline = time.monotonic() + timeout
    signature, since = None, time.monotonic()
    expression = _snapshot_expression(config)
    while True:
        if should_stop and should_stop():
            raise InterruptedError("browser data wait cancelled")
        snapshot = read(expression)
        current = json.dumps([(item["id"], item["text"]) for item in snapshot["items"]])
        if current != signature:
            signature, since = current, time.monotonic()
        response_pattern = config.get("response_url")
        response_ready = not response_pattern or any(
            fnmatch(str(item.get("url", "")), str(response_pattern)) and 200 <= int(item.get("status", 0)) < 300
            for item in responses or []
        )
        text_ready = "text_not" not in config or " ".join(str(item["text"]).strip() for item in snapshot["items"]) != config["text_not"]
        if len(snapshot["items"]) >= minimum and not snapshot["loading"] and response_ready and text_ready and time.monotonic() - since >= stable:
            return {"state": "ready", "records_visible": len(snapshot["items"]), "completeness": "unknown"}
        if time.monotonic() >= deadline:
            raise TimeoutError("browser data readiness conditions were not met")
        time.sleep(min(0.05, max(0, deadline - time.monotonic())))


def collect_virtual_items(read: Reader, config: dict[str, Any], *, maximum_bytes: int,
                          should_stop: Callable[[], bool] | None = None) -> tuple[str, dict[str, Any]]:
    for name in ("item_selector", "container_selector", "identity_attribute"):
        if not isinstance(config.get(name), str) or not config[name].strip():
            raise ValueError(f"browser collection requires {name}")
    steps = _integer(config, "max_steps", 100, 1, 1000)
    maximum_items = _integer(config, "max_items", 10000, 1, 25000)
    timeout = _integer(config, "timeout_ms", 20000, 1, 120000) / 1000
    pause = _integer(config, "pause_ms", 100, 1, 5000) / 1000
    stagnant_limit = _integer(config, "stagnant_steps", 10, 1, 100)
    expected = config.get("expected_count")
    if expected is not None and (type(expected) is not int or not 0 <= expected <= maximum_items):
        raise ValueError("browser collection expected_count is out of bounds")
    captured: dict[str, str] = {}
    total_bytes, stagnant = 0, 0
    reason, complete = "step_limit", False
    started = time.monotonic()
    expression = _snapshot_expression(config)
    for _step in range(steps):
        if should_stop and should_stop():
            raise InterruptedError("browser collection cancelled")
        snapshot = read(expression)
        progress = False
        for item in snapshot["items"]:
            identity = item.get("id")
            if not isinstance(identity, str) or not identity.strip():
                raise ValueError("virtual-list item has no stable identity")
            markup = str(item["html"])
            if identity in captured:
                if captured[identity] != markup:
                    reason = "identity_content_changed"
                    break
                continue
            if len(captured) >= maximum_items or total_bytes + len(markup.encode()) > maximum_bytes:
                reason = "collection_budget_exhausted"
                break
            captured[identity] = markup
            total_bytes += len(markup.encode())
            progress = True
        if reason in {"identity_content_changed", "collection_budget_exhausted"}:
            break
        if not snapshot["loading"] and (snapshot["ended"] or expected is not None and len(captured) == expected) and (captured or config.get("allow_empty") is True):
            reason, complete = "declared_end", True
            break
        if expected is not None and len(captured) > expected:
            reason = "expected_count_exceeded"
            break
        stagnant = 0 if progress else stagnant + 1
        if stagnant >= stagnant_limit:
            reason = "no_progress"
            break
        if time.monotonic() - started >= timeout:
            reason = "timeout"
            break
        # Produce a real position change for event-driven lists at their current bottom.
        read("(() => {const e=document.querySelector(" + json.dumps(config["container_selector"]) + ");"
             "if(!e)throw new Error('collection container missing');"
             "e.scrollTop=e.scrollTop>=e.scrollHeight-e.clientHeight-1?Math.max(0,e.scrollTop-1):e.scrollHeight;return true;})()")
        time.sleep(min(pause, max(0, timeout - (time.monotonic() - started))))
    markup = "".join(captured.values())
    html = read("(() => {const root=document.documentElement.cloneNode(true);const container=root.querySelector(" +
                json.dumps(config["container_selector"]) + ");if(!container)throw new Error('collection container missing');"
                "container.innerHTML=" + json.dumps(markup) + ";return root.outerHTML;})()")
    if not isinstance(html, str) or len(html.encode()) > maximum_bytes:
        raise ValueError("collected browser document exceeds response budget")
    return html, {"completeness": "complete" if complete else "partial", "stop_reason": reason,
                  "records_collected": len(captured), "identity_attribute": config["identity_attribute"]}
