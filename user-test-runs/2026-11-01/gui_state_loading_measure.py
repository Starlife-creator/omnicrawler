"""Reproduce the three documented GUI StateStore read/write paths on 100k events."""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import median

from omnicrawler.core.models import CrawlRequest, ExtractedRecord, FetchResult
from omnicrawler.services.reprocess_review import ReprocessReview
from omnicrawler.state import StateStore

ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "state-100k-final.sqlite3"
MEASUREMENTS = {}


def measure(name, operation, repeat=20):
    samples = []
    result = None
    for _ in range(repeat):
        started = time.perf_counter_ns()
        result = operation()
        samples.append((time.perf_counter_ns() - started) / 1_000_000)
    MEASUREMENTS[name] = {"median_ms": round(median(samples), 3), "max_ms": round(max(samples), 3)}
    return result


def main():
    now = datetime.now(UTC).isoformat()
    with StateStore(DATABASE) as state:
        run_ids = [state.start_run(f"load-{index}", "config.yaml") for index in range(200)]
        state.conn.executemany(
            "INSERT INTO run_state_events(run_id,from_state,to_state,reason,details_json,created_at) VALUES(?,?,?,?,?,?)",
            ((run_ids[index % 200], "running", "running", "measurement", "{}", now) for index in range(100_000)),
        )
        request = CrawlRequest("https://example.org/items")
        record = ExtractedRecord(request.url, "item", {"name": "before"})
        state.save_records(run_ids[0], request, [record])
        record_id = state.rows("SELECT record_id FROM records LIMIT 1")[0]["record_id"]
        state.edit_record(record_id, "name", "manual")
        result = FetchResult(request, request.url, 200, {}, b"source", 0)
        assert state.preserve_reprocess_candidate(
            run_ids[0], result, [ExtractedRecord(request.url, "item", {"name": "after"})],
            fields={"name": {"required": True}}, force=True,
        )
        checkpoint = state.checkpoint(run_ids[0], "reprocess_candidate", request.fingerprint)
        assert checkpoint is not None
        assert record_id in checkpoint["payload"]["preserved_record_ids"], (record_id, checkpoint["payload"])
        state.conn.commit()
        measure("run_compare_rows_30", lambda: state.rows(
            "SELECT run_id, started_at, status FROM runs ORDER BY started_at DESC LIMIT 30"
        ))
        snapshot = measure("reprocess_review_load", lambda: ReprocessReview(state).load(record_id))
        measure(
            "reprocess_review_resolve_reject",
            lambda: ReprocessReview(state).resolve(
                record_id, snapshot["token"], candidate_index=None, reason="measurement only"
            ),
            repeat=1,
        )
        measure("list_runs_50", lambda: state.list_runs(50))
        measure("list_runs_500", lambda: state.list_runs(500))
        measure("run_events", lambda: state.run_events(run_ids[0]))
        measure("run_stages", lambda: state.run_stages(run_ids[0]))
    report = {"runs": 200, "state_events": 100_000, "measurements": MEASUREMENTS}
    (ROOT / "gui-state-loading.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
