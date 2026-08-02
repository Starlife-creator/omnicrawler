from __future__ import annotations

import json
from typing import Any

from ..quality.semantic_changes import compare_record_data, record_identity
from ..state import StateStore


def compare_runs(state: StateStore, before_run: str, after_run: str) -> dict[str, Any]:
    before = _records(state, before_run)
    after = _records(state, after_run)
    keys = sorted(set(before) | set(after))
    run_rows = state.rows("SELECT run_id, status FROM runs WHERE run_id IN (?,?)", (before_run, after_run))
    statuses = {row["run_id"]: row["status"] for row in run_rows}
    after_complete = statuses.get(after_run) in {"succeeded", "completed"}
    changes: list[dict[str, Any]] = []
    possible_removed = 0
    for key in keys:
        old = before.get(key)
        new = after.get(key)
        change = compare_record_data(old, new, identity=key)
        if change.change_type != "unchanged":
            item = change.to_dict()
            if change.change_type == "removed" and not after_complete:
                item["change_type"] = "possibly_removed"
                item["confirmed"] = False
                possible_removed += 1
            else:
                item["confirmed"] = True
            changes.append(item)
    counts = {
        "added": sum(item["change_type"] == "added" for item in changes),
        "removed": sum(item["change_type"] == "removed" for item in changes),
        "modified": sum(item["change_type"] == "modified" for item in changes),
        "possibly_removed": possible_removed,
    }
    return {
        "before_run": before_run,
        "after_run": after_run,
        "after_run_status": statuses.get(after_run, "unknown"),
        **counts,
        "changes": changes,
    }


def _records(state: StateStore, run_id: str) -> dict[str, dict[str, Any]]:
    rows = state.rows(
        "SELECT source_url, record_type, data_json FROM records WHERE run_id=? ORDER BY record_id",
        (run_id,),
    )
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        data = json.loads(row["data_json"])
        identity = record_identity(data, str(row["source_url"]))
        result[f"{row['record_type']}|{identity}"] = data
    return result
