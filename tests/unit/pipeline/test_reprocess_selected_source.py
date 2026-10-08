import json

import pytest

from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest, ExtractedRecord, FetchResult
from omnicrawler.pipeline import Pipeline


@pytest.fixture
def archived(tmp_path):
    path = tmp_path / "task.yaml"
    path.write_text("project: {name: selective, workspace: work}\nsource: {kind: static_html, seeds: [https://example.org/a]}\nextract: {mode: html, enrich: false, fields: {title: {selector: h1, required: true}}}\n", encoding="utf-8")
    with Pipeline(load_config(path)) as pipeline:
        run = pipeline.state.start_run("selective", str(path))
        for name in ("a", "b"):
            request = CrawlRequest(f"https://example.org/{name}")
            body = f"<h1>New {name}</h1>".encode()
            raw = pipeline.workspace / "raw" / f"{name}.html"
            raw.parent.mkdir(parents=True, exist_ok=True)
            raw.write_bytes(body)
            pipeline.state.save_response(run, FetchResult(request, request.url, 200, {"content-type": "text/html"}, body, 0), str(raw))
            pipeline.state.save_records(run, request, [ExtractedRecord(request.url, "item", {"title": f"Original {name}"})])
        rows = pipeline.state.rows("SELECT * FROM records ORDER BY source_url")
        yield pipeline, run, rows


def test_selected_source_generates_candidates_without_reset_export_or_other_sources(archived, monkeypatch):
    pipeline, run, rows = archived
    def forbidden(*args, **kwargs):
        pytest.fail("Selected-source review reset, exported or fetched")
    monkeypatch.setattr(pipeline.state, "reset_record_stage", forbidden)
    monkeypatch.setattr(pipeline, "_run_exports", forbidden)
    monkeypatch.setattr(pipeline, "_fetch_checked", forbidden)
    (pipeline.workspace / "raw" / "b.html").write_bytes(b"irrelevant corrupt source")
    summary = pipeline.reprocess_records(run, record_id=rows[0]["record_id"])
    assert summary["scope"] == "source_response" and summary["manual_review_required"]
    assert summary["reprocessed_responses"] == 1 and summary["export_refreshed"] is False
    current = pipeline.state.rows("SELECT * FROM records ORDER BY source_url")
    assert current[1] == rows[1]
    assert current[0]["data_json"] == rows[0]["data_json"]
    payload = pipeline.state.checkpoint(run, "reprocess_candidate", rows[0]["request_fingerprint"])["payload"]
    assert payload["records"][0]["data"] == {"title": "New a"}
    assert not pipeline.state.rows("SELECT * FROM semantic_changes")
    assert not pipeline.state.checkpoint(run, "reprocess_candidate", rows[1]["request_fingerprint"])


def test_selected_source_refuses_corrupt_latest_version_without_changes(archived):
    pipeline, run, rows = archived
    (pipeline.workspace / "raw" / "a.html").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash mismatch"):
        pipeline.reprocess_records(run, record_id=rows[0]["record_id"])
    assert pipeline.state.rows("SELECT * FROM records ORDER BY source_url") == rows


def test_selected_source_requires_record_in_requested_run(archived):
    pipeline, run, rows = archived
    with pytest.raises(ValueError, match="does not belong"):
        pipeline.reprocess_records("other-run", record_id=rows[0]["record_id"])
    with pytest.raises(KeyError, match="Unknown"):
        pipeline.reprocess_records(run, record_id="missing")
    assert pipeline.state.rows("SELECT * FROM records ORDER BY source_url") == rows


def test_bodyless_conditional_response_reuses_only_matching_body(archived):
    pipeline, run, rows = archived
    request = CrawlRequest(rows[0]["source_url"])
    pipeline.state.save_response(run, FetchResult(request, request.url, 304, {}, b"", 0), None)
    summary = pipeline.reprocess_records(run, record_id=rows[0]["record_id"])
    assert summary["candidate_records"] == 1
    candidate = pipeline.state.checkpoint(run, "reprocess_candidate", rows[0]["request_fingerprint"])["payload"]
    assert json.dumps(candidate["records"][0]["data"]) == json.dumps({"title": "New a"})
