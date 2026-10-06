"""Task-scoped record notices reuse page-monitor delivery leases and network boundaries."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from typing import Any

from ..core.config import AppConfig
from ..scheduling.change_detector import MonitorRule
from ..scheduling.monitor_store import MonitorStore
from ..scheduling.webhook import dispatch_webhooks, webhook_target_id
from ..security.egress import EgressBroker
from ..state import StateStore


def rule_for(config: AppConfig) -> MonitorRule:
    project = config.section("project")
    task = "id:" + str(project["task_id"]) if project.get("task_id") else "legacy:" + config.project_name
    notice = config.section("updates").get("notifications", {})
    return MonitorRule("", name=config.project_name,
                       rule_id="records:" + hashlib.sha256(task.encode()).hexdigest(),
                       enabled=notice.get("enabled") is True,
                       webhook_url=notice.get("webhook_url", ""), webhook_token_ref=notice.get("webhook_token_ref", ""))


def notification_binding(config: AppConfig) -> dict[str, Any] | None:
    rule = rule_for(config)
    if not rule.enabled or not rule.webhook_url:
        return None
    return {"rule_id": rule.rule_id, "name": rule.name, "target_id": webhook_target_id(rule),
            "policy": config.section("updates").get("notifications", {}).get("policy", {}),
            "config_sha256": hashlib.sha256(json.dumps(config.raw, sort_keys=True, default=str).encode()).hexdigest()}


def report(config: AppConfig) -> dict[str, Any]:
    path = config.workspace / "state.sqlite3"
    rule = rule_for(config)
    if not path.is_file():
        return {"enabled": rule.enabled, "deliveries": []}
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='target_deliveries'").fetchone():
            return {"enabled": rule.enabled, "deliveries": []}
        rows = connection.execute(
            "SELECT event_id,rule_id,target_id,status,attempts,next_attempt,error,body_json "
            "FROM target_deliveries WHERE rule_id=? ORDER BY rowid DESC LIMIT 200", (rule.rule_id,)).fetchall()
    deliveries = []
    for row in rows:
        item = dict(row)
        body = json.loads(item.pop("body_json"))
        item.update({key: body.get(key) for key in ("task_key", "run_id", "config_sha256", "source_kind", "detected_at", "suppression_reason")})
        deliveries.append(item)
    return {"enabled": rule.enabled, "deliveries": deliveries}


def dispatch(config: AppConfig, *, egress: Any = None, force: bool = False,
             event_ids: set[str] | None = None) -> dict[str, Any]:
    rule = rule_for(config)
    path = config.workspace / "state.sqlite3"
    if not path.is_file():
        return report(config)
    if event_ids is not None:
        known = {row["event_id"] for row in report(config)["deliveries"]}
        if not event_ids or not event_ids <= known:
            raise ValueError("只能补发当前任务明确选中的历史通知")
    with StateStore(path):
        pass  # The versioned migration owns delivery schema upgrades.
    queue = MonitorStore(path, delivery_only=True)
    dispatch_webhooks(queue, [rule], egress if egress is not None else EgressBroker(config),
                      force=force, scope_rule_ids={rule.rule_id}, event_ids=event_ids)
    return report(config)
