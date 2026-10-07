import json

import pytest

from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest, ExtractedRecord, FetchResult
from omnicrawler.pipeline import Pipeline
from omnicrawler.services.reprocess_review import ReprocessReview


def config(tmp_path):
    path = tmp_path / "task.yaml"
    path.write_text("project: {name: safe-reprocess, workspace: work}\nsource: {kind: static_html, seeds: [https://example.org/]}\nextract: {mode: html, enrich: false, fields: {amount: {selector: b, type: number, required: true, critical: true}}}\n", encoding="utf-8")
    return load_config(path)


def test_corrupt_archive_is_rejected_before_existing_record_reset(tmp_path):
    with Pipeline(config(tmp_path)) as pipeline:
        run = pipeline.state.start_run("safe", "task.yaml")
        request = CrawlRequest("https://example.org/")
        raw = pipeline.workspace / "raw" / "page.html"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(b"original archive")
        pipeline.state.save_response(run, FetchResult(request, request.url, 200, {}, raw.read_bytes(), 0), str(raw))
        pipeline.state.save_records(run, request, [ExtractedRecord(request.url, "item", {"amount": 100})])
        raw.write_bytes(b"modified archive")
        with pytest.raises(RuntimeError, match="hash mismatch"):
            pipeline.reprocess_records(run)
        assert json.loads(pipeline.state.rows("SELECT data_json FROM records")[0]["data_json"]) == {"amount": 100}
        assert not pipeline.state.rows("SELECT * FROM audit_events WHERE action='reprocess_records_started'")


def test_review_candidate_has_contract_quality_and_acceptance_cannot_hide_it(tmp_path):
    with Pipeline(config(tmp_path)) as pipeline:
        run = pipeline.state.start_run("safe", "task.yaml")
        request = CrawlRequest("https://example.org/")
        pipeline.state.save_records(run, request, [ExtractedRecord(request.url, "item", {"amount": 100})])
        record_id = pipeline.state.rows("SELECT record_id FROM records")[0]["record_id"]
        pipeline.state.edit_record(record_id, "amount", 101)
        pipeline._handle_result(run, FetchResult(request, request.url, 200, {"content-type": "text/html"}, b"<b>not a number</b>", 0), 0, discover=False)
        service = ReprocessReview(pipeline.state)
        snapshot = service.load(record_id)
        quality = snapshot["candidate"]["records"][0]["evidence"]["_quality"]
        assert quality["review_required"] and quality["validation_errors"]
        assert not pipeline.state.quality_stats(run)
        updated = service.resolve(record_id, snapshot["token"], candidate_index=0, reason="Mapping confirmed; value still requires correction")
        assert updated["evidence"]["_quality"]["review_required"]
        assert pipeline.state.review_queue(run)
