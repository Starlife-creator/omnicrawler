"""Update notification policy checkpoints in the observation transaction."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Any

from ..core.models import ExtractedRecord
from ..core.utils import json_text
from ..quality.notification_rules import threshold_reason, validate_policy
from ..quality.semantic_changes import (
    SemanticChange,
    compare_record_data,
    entity_checkpoint_key,
    semantic_hash,
)


def evaluate_policy(connection: sqlite3.Connection, run_id: str, record: ExtractedRecord, *,
                    task_key: str, scope: str, identity: str, before: dict[str, Any] | None,
                    notification: dict[str, Any], ignored_fields: set[str] | None,
                    cross_page: bool, now: str, baseline: bool) -> tuple[SemanticChange, str]:
    policy = notification.get("policy", {})
    errors = validate_policy(policy)
    if errors:
        raise ValueError("; ".join(errors))
    key = hashlib.sha256(json_text([
        task_key, scope, record.record_type, identity, "" if cross_page else record.source_url,
        notification["rule_id"], notification["target_id"], policy,
    ]).encode()).hexdigest()
    # The existing entity index finds the previous observation. Its run then
    # addresses the checkpoint primary key; no scan of all checkpoint JSON.
    previous = connection.execute(
        "SELECT o.run_id, u.rowid AS cycle FROM entity_observations o JOIN runs u ON u.run_id=o.run_id "
        "WHERE o.task_key=? AND comparison_scope=? "
        "AND record_type=? AND identity=? AND o.run_id<>? AND (source_url=? OR ?) "
        "UNION ALL SELECT c.run_id, u.rowid AS cycle FROM stage_checkpoints c "
        "JOIN runs u ON u.run_id=c.run_id JOIN run_identities i ON i.run_id=c.run_id "
        "WHERE c.stage='record_deletion' AND c.idempotency_key=? AND c.run_id<>? "
        "AND i.task_key=? AND ? AND json_extract(c.payload_json, '$.comparison_scope')=? "
        "ORDER BY cycle DESC LIMIT 1",
        (task_key, scope, record.record_type, identity, run_id, record.source_url, cross_page,
         entity_checkpoint_key(record.record_type, identity), run_id, task_key, cross_page, scope),
    ).fetchone()
    saved = connection.execute(
        "SELECT payload_json FROM stage_checkpoints WHERE run_id=? AND stage='record_notification_policy' "
        "AND idempotency_key=?", (previous["run_id"], key),
    ).fetchone() if previous is not None else None
    state: dict[str, Any] = json.loads(saved["payload_json"]) if saved is not None else {
        "baseline": record.data if baseline else before,
        "candidate_hash": "", "candidate": None, "candidate_count": 0, "last_enqueued_at": None,
    }
    fields = policy.get("fields", {})
    projected = {name: record.data[name] for name in fields if name in record.data} if fields else record.data
    digest = semantic_hash(projected, ignored_fields=ignored_fields)
    same_candidate = compare_record_data(state.get("candidate"), projected, ignored_fields=ignored_fields).change_type == "unchanged"
    state["candidate_count"] = min(100, state["candidate_count"] + 1) if same_candidate else 1
    state["candidate_hash"] = digest
    state["candidate"] = projected
    change = compare_record_data(state["baseline"], record.data, identity=identity, ignored_fields=ignored_fields)
    reason = ""
    if change.change_type == "unchanged":
        reason = "no_change"
    elif fields:
        reason = threshold_reason(state["baseline"] or {}, record.data, fields)
    if not reason and state["candidate_count"] < policy.get("confirmations", 1):
        reason = "awaiting_confirmation"
    if not reason and state["last_enqueued_at"] is not None:
        elapsed = (datetime.fromisoformat(now) - datetime.fromisoformat(state["last_enqueued_at"])).total_seconds()
        if elapsed < policy.get("cooldown_seconds", 0):
            reason = "cooldown"
    if not reason:
        state["baseline"] = record.data
        state["last_enqueued_at"] = now
    connection.execute(
        "INSERT INTO stage_checkpoints(run_id,stage,idempotency_key,status,payload_json,updated_at) "
        "VALUES(?,'record_notification_policy',?,'succeeded',?,?) "
        "ON CONFLICT(run_id,stage,idempotency_key) DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at",
        (run_id, key, json_text(state), now),
    )
    return change, reason
