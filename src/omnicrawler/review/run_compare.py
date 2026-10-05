from __future__ import annotations

import json
from typing import Any

from ..quality.semantic_changes import compare_record_data, record_identity, semantic_hash
from ..state import StateStore


def compare_runs(state: StateStore, before_run: str, after_run: str) -> dict[str, Any]:
    identity_rows = state.rows(
        "SELECT run_id, task_key FROM run_identities WHERE run_id IN (?,?)", (before_run, after_run),
    )
    task_keys = {row["run_id"]: row["task_key"] for row in identity_rows}
    if len(task_keys) != len({before_run, after_run}):
        raise ValueError("运行不存在或历史任务身份未映射，不能确认比较归属")
    if task_keys[before_run] != task_keys[after_run]:
        raise ValueError("不能比较不同任务的运行")
    before_settings = (state.checkpoint(before_run, "setup", "setup") or {}).get("payload", {}).get("semantic_settings", {})
    after_settings = (state.checkpoint(after_run, "setup", "setup") or {}).get("payload", {}).get("semantic_settings", {})
    before = _records(state, before_run, tuple(before_settings.get("identity_fields", [])))
    after = _records(state, after_run, tuple(after_settings.get("identity_fields", [])))
    ignored = set(after_settings["ignored_fields"]) if "ignored_fields" in after_settings else None
    keys = sorted(set(before) | set(after))
    run_rows = state.rows("SELECT run_id, status, summary_json FROM runs WHERE run_id IN (?,?)", (before_run, after_run))
    statuses = {row["run_id"]: row["status"] for row in run_rows}
    reasons = _deletion_reasons(state, before_run, after_run, run_rows)
    identity_changed = before_settings.get("identity_fields", []) != after_settings.get("identity_fields", [])
    if identity_changed:
        reasons.append("identity_contract_changed")
    context_changed = identity_changed or "scope_changed_or_unknown" in reasons
    after_complete = not reasons
    changes: list[dict[str, Any]] = []
    possible_removed = 0
    for key in keys:
        old = before.get(key)
        new = after.get(key)
        source_url, record_type, identity = json.loads(key)
        change = compare_record_data(old, new, identity=f"{record_type}|{identity}", ignored_fields=ignored)
        if change.change_type != "unchanged":
            item = change.to_dict()
            item["source_url"] = source_url
            if change.change_type == "removed" and not after_complete:
                item["change_type"] = "possibly_removed"
                item["confirmed"] = False
                possible_removed += 1
            else:
                item["confirmed"] = not context_changed
            changes.append(item)
    counts = {
        "added": sum(item["change_type"] == "added" for item in changes),
        "removed": sum(item["change_type"] == "removed" for item in changes),
        "modified": sum(item["change_type"] == "modified" for item in changes),
        "possibly_removed": possible_removed,
    }
    return {
        "identity_confidence": "stable" if task_keys[before_run].startswith("id:") else "legacy_name_only",
        "before_run": before_run,
        "after_run": after_run,
        "after_run_status": statuses.get(after_run, "unknown"),
        "deletion_confirmation_reasons": reasons,
        **counts,
        "changes": changes,
        "notification_summary": {"total": len(changes), **counts, "requires_review": bool(possible_removed) or context_changed,
                                 "changed_fields": sorted({field for item in changes for field in item.get("modified_fields", [])})},
    }


def _records(state: StateStore, run_id: str, identity_fields: tuple[str, ...] = ()) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}

    def collect(record: dict[str, Any]) -> None:
        data = record["data"]
        source = str(record["source_url"])
        identity = record_identity(data, source, identity_fields=identity_fields)
        key = json.dumps(["" if identity_fields else source, record["record_type"], identity], ensure_ascii=False)
        previous = result.get(key)
        if previous is not None and semantic_hash(previous) != semantic_hash(data):
            raise ValueError("同轮业务身份存在冲突值；请复核，不能静默覆盖")
        result[key] = data

    observations = state.rows(
        "SELECT source_url, record_type, data_json FROM entity_observations WHERE run_id=? ORDER BY id", (run_id,),
    )
    if observations:
        for row in observations:
            collect({**row, "data": json.loads(row["data_json"])})
        return result
    snapshots = state.rows(
        "SELECT payload_json FROM stage_checkpoints "
        "WHERE run_id=? AND stage='record_observation' ORDER BY updated_at, rowid", (run_id,),
    )
    if snapshots:
        for snapshot in snapshots:
            for record in json.loads(snapshot["payload_json"])["records"]:
                collect(record)
        return result
    rows = state.rows(
        "SELECT source_url, record_type, data_json FROM records WHERE run_id=? ORDER BY record_id", (run_id,),
    )
    for row in rows:
        collect({**row, "data": json.loads(row["data_json"])})
    return result


def _deletion_reasons(
    state: StateStore, before_run: str, after_run: str, runs: list[dict[str, Any]],
) -> list[str]:
    after = next((row for row in runs if row["run_id"] == after_run), {})
    reasons: list[str] = []
    if after.get("status") not in {"succeeded", "completed"}:
        reasons.append("run_incomplete")
    summary = json.loads(after.get("summary_json") or "{}")
    delivery = (summary.get("export") or {}).get("delivery") or {}
    frontier = summary.get("frontier") or {}
    if delivery.get("budget_exhausted") or delivery.get("frontier_pending") or any(
        frontier.get(key) for key in ("pending", "running", "failed")
    ):
        reasons.append("collection_incomplete")
    if summary.get("errors") or state.rows("SELECT 1 FROM errors WHERE run_id=? LIMIT 1", (after_run,)):
        reasons.append("collection_errors")
    before_setup = (state.checkpoint(before_run, "setup", "setup") or {}).get("payload", {})
    after_setup = (state.checkpoint(after_run, "setup", "setup") or {}).get("payload", {})
    if before_setup.get("comparison_scope") != after_setup.get("comparison_scope"):
        reasons.append("scope_changed_or_unknown")
    if after_setup.get("resume"):
        reasons.append("partial_resume")
    if after_setup and not after_setup.get("revisit_completed") and state.rows(
        "SELECT 1 FROM stage_checkpoints b WHERE b.run_id=? AND b.stage='record_observation' "
        "AND NOT EXISTS (SELECT 1 FROM stage_checkpoints a WHERE a.run_id=? "
        "AND a.stage=b.stage AND a.idempotency_key=b.idempotency_key) LIMIT 1",
        (before_run, after_run),
    ):
        reasons.append("previous_pages_not_revisited")
    missing = state.rows(
        "SELECT 1 FROM responses r WHERE r.run_id=? AND NOT EXISTS "
        "(SELECT 1 FROM stage_checkpoints c WHERE c.run_id=r.run_id "
        "AND c.stage='record_observation' AND c.idempotency_key=r.request_fingerprint) LIMIT 1",
        (after_run,),
    )
    if missing:
        reasons.append("observations_unverified")
    if state.rows("SELECT 1 FROM responses WHERE run_id=? AND status_code=304 LIMIT 1", (after_run,)):
        # No response body means discovery coverage cannot be inferred from success alone.
        reasons.append("conditional_response_coverage_unverified")
    if after_setup and not state.rows(
        "SELECT 1 FROM stage_checkpoints WHERE run_id=? AND stage='record_observation' LIMIT 1", (after_run,),
    ):
        reasons.append("no_observations")
    return reasons
