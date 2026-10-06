"""User-configured webhook deliveries with independent durable receipts."""
from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from ..core.credentials import resolve_secret_refs
from ..fetching.http_client import build_safe_opener
from .monitor_store import MonitorStore


def webhook_target_id(rule: Any) -> str:
    value = json.dumps([rule.webhook_url, rule.webhook_token_ref], ensure_ascii=False)
    return "webhook:" + hashlib.sha256(value.encode()).hexdigest()


def dispatch_webhooks(store: MonitorStore, rules: list[Any], egress: Any, *,
                      force: bool = False, scope_rule_ids: set[str] | None = None, event_ids: set[str] | None = None, cancelled: Callable[[], bool] = lambda: False) -> None:
    active = {rule.rule_id: rule for rule in rules if rule.enabled and rule.webhook_url}
    store.revoke_targets({key: {webhook_target_id(rule)} for key, rule in active.items()}, rule_ids=scope_rule_ids)
    for row in store.pending(set(active), force=force, target=True, event_ids=event_ids):
        if cancelled():
            break
        rule = active[row["rule_id"]]
        target = webhook_target_id(rule)
        if row["target_id"] != target:
            continue
        lease = store.claim(row["event_id"], target_id=target)
        if not lease:
            continue
        try:
            if egress is None:
                raise RuntimeError("Webhook requires application EgressBroker")
            headers = {"Content-Type": "application/json", "Idempotency-Key": row["event_id"]}
            if rule.webhook_token_ref:
                token = resolve_secret_refs(rule.webhook_token_ref)
                if not isinstance(token, str) or not token or token.startswith("secret://"):
                    raise ValueError("Webhook secret is unavailable")
                headers["Authorization"] = "Bearer " + token
            request = urllib.request.Request(rule.webhook_url, data=row["body_json"].encode(), headers=headers, method="POST")
            with egress.request(rule.webhook_url, purpose="notification", headers=headers):
                opener = build_safe_opener(egress.config, target_policy=egress.policy, include_cookies=False,
                                           egress=egress, purpose="notification")
                with opener.open(request, timeout=15) as response:
                    if not 200 <= response.status < 300:
                        raise RuntimeError("Webhook did not accept notification")
                    raw = response.read(1025)
                    egress.record_response(len(raw), url=response.geturl())
            store.acknowledge(row["event_id"], target_id=target, lease_token=lease)
        except urllib.error.HTTPError as exc:
            delay = None
            if exc.headers is not None:
                try:
                    delay = min(300, max(0, float(exc.headers.get("Retry-After", ""))))
                except ValueError:
                    pass
            exc.close()
            store.fail(row["event_id"], target_id=target, lease_token=lease, error=f"http_{exc.code}", delay=delay,
                       permanent=400 <= exc.code < 500 and exc.code not in {408, 429})
        except Exception:
            # Never write provider bodies, endpoints with query secrets or tokens to the receipt.
            store.fail(row["event_id"], target_id=target, lease_token=lease, error="webhook_failed")
