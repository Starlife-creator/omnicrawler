"""Confirm missing entities only after a complete, comparable collection finishes."""
from __future__ import annotations

import uuid
from typing import Any

from ..core.config import AppConfig
from ..core.utils import json_text, utcnow
from ..quality.semantic_changes import compare_record_data, entity_checkpoint_key, semantic_hash
from ..review.run_compare import compare_runs
from ..state import StateStore
from ..state.notification_queue import enqueue_event
from .record_notifications import notification_binding


def finalize_removed(config: AppConfig, state: StateStore, run_id: str) -> dict[str, Any]:
    """Atomically retain the fact, presence tombstone and durable target event."""
    binding = notification_binding(config)
    if not binding or config.section("updates").get("notifications", {}).get("include_removed") is not True:
        return {"removed": 0, "reasons": ["disabled"]}
    task_key, scope = state._observation_context(run_id)
    if not task_key.startswith("id:") or not scope or not config.section("updates").get("identity_fields"):
        return {"removed": 0, "reasons": ["stable_identity_required"]}
    with state._lock, state.conn:
        saved = state.checkpoint(run_id, "record_removal", "finalize")
        if saved is not None:
            return saved["payload"]
        previous = state.conn.execute(
            "SELECT r.run_id FROM runs r JOIN run_identities i ON i.run_id=r.run_id "
            "WHERE i.task_key=? AND r.rowid<(SELECT rowid FROM runs WHERE run_id=?) "
            "AND r.status IN ('succeeded','completed') ORDER BY r.rowid DESC LIMIT 1", (task_key, run_id),
        ).fetchone()
        result: dict[str, Any] = {"removed": 0, "reasons": ["baseline"]}
        now = utcnow()
        if previous is not None:
            comparison = compare_runs(state, previous["run_id"], run_id)
            result = {"removed": 0, "before_run": previous["run_id"],
                      "reasons": comparison["deletion_confirmation_reasons"]}
            for item in comparison["changes"]:
                if item["change_type"] != "removed" or item.get("confirmed") is not True:
                    continue
                record_type, identity = item["identity"].split("|", 1)
                source = state.conn.execute(
                    "SELECT source_url FROM entity_observations WHERE run_id=? AND record_type=? AND identity=? LIMIT 1",
                    (previous["run_id"], record_type, identity),
                ).fetchone()
                source_url = source["source_url"] if source else item["source_url"]
                change = compare_record_data(item["before"], None, identity=identity)
                details = {**change.to_dict(), "confirmed": True, "before_run": previous["run_id"]}
                event_id = uuid.uuid5(uuid.NAMESPACE_URL, json_text([run_id, task_key, scope, record_type, identity, "removed"])).hex
                event = {
                    "envelope_version": 1, "source_kind": "record_fields", "event_id": event_id,
                    "rule_id": binding["rule_id"], "rule_name": binding["name"], "task_key": task_key,
                    "run_id": run_id, "comparison_scope": scope, "config_sha256": binding["config_sha256"],
                    "url": source_url, "detected_at": now, "previous_hash": semantic_hash(item["before"]),
                    "current_hash": None, "previous_content": json_text(item["before"]), "current_content": None,
                    "diff_summary": "removed", "notification_eligible": True, "suppression_reason": "",
                    "details": details, "observed_change_type": "removed",
                }
                enqueue_event(state.conn, binding["rule_id"], event, targets=[binding["target_id"]], desktop=False)
                state.conn.execute(
                    "INSERT INTO semantic_changes(run_id,source_url,record_type,identity,change_type,similarity,"
                    "added_json,removed_json,modified_json,before_json,after_json,baseline,created_at) "
                    "VALUES(?,?,?,?,'removed',0,'[]',?,'[]',?,NULL,0,?)",
                    (run_id, source_url, record_type, identity, json_text(change.removed_fields), json_text(item["before"]), now),
                )
                payload = {"comparison_scope": scope, "task_key": task_key, "record_type": record_type,
                           "identity": identity, "source_url": source_url, "before_run": previous["run_id"], "before": item["before"]}
                state.conn.execute(
                    "INSERT INTO stage_checkpoints(run_id,stage,idempotency_key,status,payload_json,updated_at) "
                    "VALUES(?,'record_deletion',?,'succeeded',?,?)",
                    (run_id, entity_checkpoint_key(record_type, identity), json_text(payload), now),
                )
                result["removed"] += 1
        state.conn.execute(
            "INSERT INTO stage_checkpoints(run_id,stage,idempotency_key,status,payload_json,updated_at) "
            "VALUES(?,'record_removal','finalize','succeeded',?,?)", (run_id, json_text(result), now),
        )
        return result
