import json

import pytest

from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest, ExtractedRecord, FetchResult
from omnicrawler.pipeline import Pipeline
from omnicrawler.quality.quality import assess_record


def test_optional_missing_and_unknown_evidence_require_review_without_discarding():
    record = ExtractedRecord("https://example.org/", "item", {"title": "A", "price": 0})
    before = dict(record.data)
    quality = assess_record(record, {"title": {}, "price": {}, "date": {}}, 0, review_policy="recall")
    assert quality["review_required"]
    assert quality["missing_fields"] == ["date"]
    assert quality["missing_required"] == []
    assert quality["field_checks"]["price"]["presence"] == "present"
    assert quality["field_checks"]["title"]["evidence"] == "unassessed"
    assert record.data == before


def test_supported_complete_record_can_pass_recall_policy():
    url = "https://example.org/"
    record = ExtractedRecord(url, "item", {"title": "A"}, {"title": {
        "source_url": url, "matches": 1, "clean_value": "A",
    }})
    assert not assess_record(record, {"title": {}}, review_policy="recall")["review_required"]


@pytest.mark.parametrize("trace", [
    {"conflicts": ["B"]}, {"clean_value": "B"}, {"label": "other"},
])
def test_positive_evidence_problem_cannot_be_hidden_by_optional_field(trace):
    url = "https://example.org/"
    record = ExtractedRecord(url, "item", {"title": "A"}, {"title": {
        "source_url": url, "matches": 1, **trace,
    }})
    quality = assess_record(record, {"title": {"expected_label": "title"}}, threshold=0)
    assert quality["review_required"] and quality["evidence_issues"]


@pytest.mark.parametrize("value", [-1, 101])
def test_integer_range_is_enforced_without_losing_candidate(value):
    record = ExtractedRecord("https://example.org/", "item", {"amount": value})
    result = assess_record(record, {"amount": {"type": "integer", "min": 0, "max": 100}}, 0)
    assert result["validation_errors"] and result["review_required"]
    assert record.data["amount"] == value


def test_default_pipeline_preserves_and_queues_incomplete_record_and_candidate(tmp_path):
    path = tmp_path / "task.yaml"
    path.write_text("project: {name: recall, workspace: work}\nsource: {seeds: [https://example.org/]}\nextract: {mode: html, fields: {title: {selector: b}, date: {selector: time}}}\n", encoding="utf-8")
    with Pipeline(load_config(path)) as pipeline:
        run = pipeline.state.start_run("recall", "task.yaml")
        request = CrawlRequest("https://example.org/")
        response = FetchResult(request, request.url, 200, {"content-type": "text/html"}, b"<b>A</b>", 0)
        pipeline._handle_result(run, response, 0, discover=False)
        rows = pipeline.state.rows("SELECT record_id,data_json,evidence_json FROM records")
        assert len(rows) == 1 and json.loads(rows[0]["data_json"])["title"] == "A"
        assert pipeline.state.review_queue(run)
        quality = json.loads(rows[0]["evidence_json"])["_quality"]
        assert quality["review_policy"] == "recall" and "date" in quality["missing_fields"]
        pipeline.state.edit_record(rows[0]["record_id"], "title", "Reviewed")
        pipeline._handle_result(run, response, 0, discover=False)
        candidate = pipeline.state.checkpoint(run, "reprocess_candidate", request.fingerprint)["payload"]
        assert candidate["records"][0]["evidence"]["_quality"]["review_policy"] == "recall"
        assert candidate["records"][0]["evidence"]["_quality"]["review_required"]


@pytest.mark.parametrize("policy", ["unknown", [], False])
def test_invalid_policy_rejected_at_configuration_validation(tmp_path, policy):
    import yaml

    path = tmp_path / "task.yaml"
    path.write_text(yaml.safe_dump({"source": {"seeds": ["https://example.org/"]},
                                  "extract": {"review_policy": policy}}), encoding="utf-8")
    with pytest.raises(ValueError, match="review_policy"):
        load_config(path)
