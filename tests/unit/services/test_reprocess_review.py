import json

import pytest

from omnicrawler.core.models import CrawlRequest, ExtractedRecord, FetchResult
from omnicrawler.services.reprocess_review import ReprocessReview
from omnicrawler.state import StateStore


@pytest.fixture
def pending(tmp_path):
    with StateStore(tmp_path / "state.sqlite3") as state:
        run = state.start_run("review", "config.yaml")
        request = CrawlRequest("https://example.org/items")
        state.save_records(run, request, [ExtractedRecord(request.url, "item", {"name": "A", "obsolete": 1}),
                                         ExtractedRecord(request.url, "item", {"name": "B"})])
        ids = [row["record_id"] for row in state.rows("SELECT record_id FROM records ORDER BY rowid")]
        state.edit_record(ids[0], "name", "Manual A")
        result = FetchResult(request, request.url, 200, {}, b"source", 0)
        candidates = [ExtractedRecord(request.url, "item", {"name": "B", "amount": 0}, {"name": {"source": "B"}}),
                      ExtractedRecord(request.url, "item", {"name": "A", "amount": None}, {"name": {"source": "A"}})]
        state.preserve_reprocess_candidate(run, result, candidates, fields={"name": {"required": True}})
        yield state, run, request, result, candidates, ids


def test_explicit_mapping_replaces_record_with_evidence_and_keeps_audit(pending):
    state, run, request, _, _, ids = pending
    service = ReprocessReview(state)
    snapshot = service.load(ids[0])
    record = service.resolve(ids[0], snapshot["token"], candidate_index=1, reason="Matched source identity A")
    assert record["data"] == {"name": "A", "amount": None}
    assert "obsolete" not in record["data"]
    assert record["evidence"]["name"] == {"source": "A"}
    assert record["evidence"]["_review"]["edits"][0]["new_value"] == "Manual A"
    assert "reprocess_candidate" not in record["evidence"]["_review"]
    assert len(state.rows("SELECT * FROM record_edits")) == 2
    service.resolve(ids[1], service.load(ids[1])["token"], candidate_index=None, reason="Keep original B")
    payload = state.checkpoint(run, "reprocess_candidate", request.fingerprint)["payload"]
    assert payload["status"] == "reviewed" and payload["unmapped_candidate_indexes"] == [0]
    assert not state.review_queue(run)
    assert not state.rows("SELECT * FROM semantic_changes")
    events = state.rows("SELECT details_json FROM audit_events WHERE action='reprocess_review'")
    assert len(events) == 2 and json.loads(events[0]["details_json"])["before"]["name"] == "Manual A"


def test_rejection_preserves_values_and_prior_quality(pending):
    state, _, _, _, _, ids = pending
    service = ReprocessReview(state)
    result = service.resolve(ids[0], service.load(ids[0])["token"], candidate_index=None, reason="Candidate inaccurate")
    assert result["data"] == {"name": "Manual A", "obsolete": 1}
    assert not result["evidence"]["_quality"]["review_required"]
    assert len(state.rows("SELECT * FROM record_edits")) == 1


def test_stale_edit_and_stale_generation_cannot_be_applied(pending):
    state, _, _, result, candidates, ids = pending
    service = ReprocessReview(state)
    snapshot = service.load(ids[0])
    state.edit_record(ids[0], "name", "New manual value")
    with pytest.raises(ValueError, match="changed"):
        service.resolve(ids[0], snapshot["token"], candidate_index=1, reason="Old view")
    snapshot = service.load(ids[0])
    state.preserve_reprocess_candidate(snapshot["run_id"], result, candidates)
    with pytest.raises(ValueError, match="changed"):
        service.resolve(ids[0], snapshot["token"], candidate_index=1, reason="Old generation")
    assert len(state.rows("SELECT * FROM stage_checkpoints WHERE stage='reprocess_review_history'")) == 1


def test_candidate_cannot_be_reused_or_record_decided_twice(pending):
    state, _, _, _, _, ids = pending
    service = ReprocessReview(state)
    snapshot = service.load(ids[0])
    service.resolve(ids[0], snapshot["token"], candidate_index=0, reason="Explicit mapping")
    with pytest.raises(ValueError, match="already"):
        service.resolve(ids[0], snapshot["token"], candidate_index=0, reason="Repeat")
    with pytest.raises(ValueError, match="already mapped"):
        service.resolve(ids[1], service.load(ids[1])["token"], candidate_index=0, reason="Duplicate mapping")


def test_audit_failure_rolls_back_record_and_decision(pending):
    state, _, _, _, _, ids = pending
    service = ReprocessReview(state)
    snapshot = service.load(ids[0])
    state.conn.execute("CREATE TRIGGER reject_review BEFORE INSERT ON audit_events BEGIN SELECT RAISE(ABORT, 'blocked audit'); END")
    state.conn.commit()
    with pytest.raises(Exception, match="blocked audit"):
        service.resolve(ids[0], snapshot["token"], candidate_index=1, reason="Atomic operation")
    assert service.load(ids[0])["token"] == snapshot["token"]
    assert len(state.rows("SELECT * FROM record_edits")) == 1


def test_repeated_pending_generation_does_not_restore_synthetic_review_flag(pending):
    state, run, _, result, candidates, ids = pending
    state.preserve_reprocess_candidate(run, result, candidates)
    service = ReprocessReview(state)
    rejected = service.resolve(ids[0], service.load(ids[0])["token"], candidate_index=None, reason="Keep corrected")
    assert rejected["evidence"]["_quality"]["review_required"] is False


@pytest.mark.parametrize("index", [True, -1, 2, "1"])
def test_invalid_mapping_never_changes_record(pending, index):
    state, _, _, _, _, ids = pending
    service = ReprocessReview(state)
    snapshot = service.load(ids[0])
    with pytest.raises(ValueError, match="Invalid candidate"):
        service.resolve(ids[0], snapshot["token"], candidate_index=index, reason="Invalid selection")
    assert service.load(ids[0])["token"] == snapshot["token"]


def test_existing_quality_warning_survives_rejection_and_acceptance(pending):
    state, run, _, result, candidates, ids = pending
    service = ReprocessReview(state)
    service.resolve(ids[0], service.load(ids[0])["token"], candidate_index=None, reason="Keep values")
    state.conn.execute("UPDATE records SET evidence_json=? WHERE record_id=?", (json.dumps({"_quality": {"review_required": True}}), ids[0]))
    state.conn.commit()
    candidates[1].evidence["_quality"] = {"review_required": True, "missing_required": ["amount"]}
    state.preserve_reprocess_candidate(run, result, candidates)
    rejected = service.resolve(ids[0], service.load(ids[0])["token"], candidate_index=None, reason="Keep warnings")
    assert rejected["evidence"]["_quality"]["review_required"] is True
    state.preserve_reprocess_candidate(run, result, candidates)
    accepted = service.resolve(ids[0], service.load(ids[0])["token"], candidate_index=1, reason="Confirm mapping only")
    assert accepted["evidence"]["_quality"]["missing_required"] == ["amount"]
    assert accepted["evidence"]["_quality"]["review_required"] is True
