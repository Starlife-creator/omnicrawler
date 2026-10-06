"""Shared delivery schema and inserts inside the caller's observation transaction."""
from __future__ import annotations

import json
import sqlite3
from typing import Any

DELIVERY_SCHEMA = """
CREATE TABLE IF NOT EXISTS target_deliveries(
    event_id TEXT NOT NULL,target_id TEXT NOT NULL,rule_id TEXT NOT NULL,body_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt REAL NOT NULL DEFAULT 0,lease_until REAL NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',
    lease_token TEXT NOT NULL DEFAULT '',PRIMARY KEY(event_id,target_id));
CREATE TABLE IF NOT EXISTS deliveries(
    event_id TEXT PRIMARY KEY,rule_id TEXT NOT NULL,body_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt REAL NOT NULL DEFAULT 0,lease_until REAL NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',
    lease_token TEXT NOT NULL DEFAULT '');
"""


def enqueue_event(connection: sqlite3.Connection, rule_id: str, event: dict[str, Any], *,
                  targets: list[str], desktop: bool = True, pending: bool = True) -> None:
    body = json.dumps(event, ensure_ascii=False)
    eligible = event.get("notification_eligible", True)
    if desktop:
        status = "suppressed" if not eligible else "pending" if pending else "submitted"
        connection.execute("INSERT OR IGNORE INTO deliveries(event_id,rule_id,body_json,status) VALUES(?,?,?,?)",
                           (event["event_id"], rule_id, body, status))
    for target in targets:
        connection.execute("INSERT OR IGNORE INTO target_deliveries(event_id,target_id,rule_id,body_json,status) VALUES(?,?,?,?,?)",
                           (event["event_id"], target, rule_id, body, "pending" if eligible else "suppressed"))
