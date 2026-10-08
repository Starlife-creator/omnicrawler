"""Re-extract one archived source into review candidates without resetting a run."""
from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from ..core.models import CrawlRequest, FetchResult


class SourceReviewState(Protocol):
    def rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]: ...
    def checkpoint(self, run_id: str, stage: str, idempotency_key: str) -> dict[str, Any] | None: ...
    def add_audit_event(self, action: str, *, run_id: str | None = None, actor: str = "system", details: dict[str, Any] | None = None) -> None: ...


class SourceReviewConfig(Protocol):
    def section(self, name: str) -> dict[str, Any]: ...


class SourceReviewPipeline(Protocol):
    @property
    def state(self) -> SourceReviewState: ...
    @property
    def config(self) -> SourceReviewConfig: ...
    workspace: Path
    _handle_result: Callable[..., None]


def reprocess_source(pipeline: SourceReviewPipeline, record_id: str, *, run_id: str | None,
                     choose_processor: Callable[[FetchResult], str]) -> dict[str, Any]:
    if not record_id.strip():
        raise ValueError("record_id cannot be empty")
    records = pipeline.state.rows("SELECT run_id,request_fingerprint FROM records WHERE record_id=?", (record_id,))
    if not records:
        raise KeyError("Unknown record")
    record = records[0]
    if run_id is not None and record["run_id"] != run_id:
        raise ValueError("Record does not belong to the requested run")
    run_id, fingerprint = str(record["run_id"]), str(record["request_fingerprint"])
    latest = pipeline.state.rows("SELECT * FROM responses WHERE run_id=? AND request_fingerprint=? ORDER BY id DESC LIMIT 1", (run_id, fingerprint))
    if not latest:
        raise ValueError("Record source has no archived response")
    # A bodyless 304 may reuse an archive only when its recorded digest matches.
    archives = pipeline.state.rows(
        "SELECT * FROM responses WHERE run_id=? AND request_fingerprint=? AND content_sha256=? AND raw_path IS NOT NULL AND status_code<>304 ORDER BY id DESC LIMIT 1",
        (run_id, fingerprint, latest[0]["content_sha256"]),
    )
    if not archives:
        raise ValueError("Latest source version has no matching archive")
    row = archives[0]
    raw_path = Path(row["raw_path"])
    path = raw_path.resolve()
    if raw_path.is_symlink() or pipeline.workspace.resolve() not in path.parents or not path.is_file():
        raise ValueError("Archive is unavailable or outside the workspace")
    maximum = int(pipeline.config.section("http")["max_response_bytes"])
    with path.open("rb") as archived:
        body = archived.read(maximum + 1)
    if len(body) > maximum:
        raise ValueError("Archive exceeds response budget")
    if hashlib.sha256(body).hexdigest() != row["content_sha256"]:
        raise ValueError("Archive hash mismatch")
    request = CrawlRequest(str(row["url"]), meta={"_fingerprint_override": fingerprint, "reprocessed": True, "review_candidates_only": True})
    result = FetchResult(request, str(latest[0]["final_url"]), int(row["status_code"]),
                         {"content-type": str(row["content_type"] or "application/octet-stream")}, body, float(row["elapsed_seconds"] or 0))
    if choose_processor(result) == "binary":
        raise ValueError("Record source cannot be re-extracted as structured records")
    before = pipeline.state.checkpoint(run_id, "reprocess_candidate", fingerprint)
    pipeline._handle_result(run_id, result, 0, persist_response=False, discover=False)
    candidate = pipeline.state.checkpoint(run_id, "reprocess_candidate", fingerprint)
    if candidate is None or before and candidate["payload"]["candidate_id"] == before["payload"].get("candidate_id"):
        raise RuntimeError("Source processing did not produce review candidates")
    summary = {"run_id": run_id, "record_id": record_id, "request_fingerprint": fingerprint, "scope": "source_response",
               "status": "review_required", "manual_review_required": True, "reprocessed_responses": 1,
               "candidate_records": len(candidate["payload"]["records"]), "candidate_id": candidate["payload"]["candidate_id"],
               "export_refreshed": False}
    pipeline.state.add_audit_event("reprocess_source_candidates", run_id=run_id, actor="local-user", details=summary)
    return summary
