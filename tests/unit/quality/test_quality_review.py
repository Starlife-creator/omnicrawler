from __future__ import annotations

import json

from omnicrawl.core.models import CrawlRequest, ExtractedRecord
from omnicrawl.quality.quality import assess_record, assess_records
from omnicrawl.state import StateStore


def test_typed_quality_rules_and_duplicate_detection() -> None:
    fields = {
        "price": {"type": "money", "required": True, "min": 0, "max": 1000},
        "published": {"type": "date", "required": True},
        "status": {"type": "enum", "values": ["open", "closed"]},
        "confirm": {"equals_field": "status"},
    }
    invalid = ExtractedRecord("https://example.org/1", "item", {
        "price": "CNY 2000", "published": "not-a-date", "status": "other", "confirm": "open",
    })
    quality = assess_record(invalid, fields)
    assert quality["review_required"] is True
    assert len(quality["validation_errors"]) == 4

    records = [
        ExtractedRecord("https://example.org/1", "item", {"id": "same"}),
        ExtractedRecord("https://example.org/2", "item", {"id": "same"}),
    ]
    summary = assess_records(records, {"id": {"required": True}}, unique_by=["id"])
    assert summary["duplicates"] == 1
    assert records[1].evidence["_quality"]["duplicate"] is True


def test_manual_review_edit_is_audited(tmp_path) -> None:
    with StateStore(tmp_path / "state.sqlite3") as state:
        run_id = state.start_run("review", "project.yaml")
        request = CrawlRequest("https://example.org/item")
        record = ExtractedRecord(
            request.url,
            "item",
            {"title": "old"},
            {"_quality": {"review_required": True, "score": 0.4}},
        )
        state.save_records(run_id, request, [record])
        queued = state.review_queue(run_id)
        assert len(queued) == 1

        state.edit_record(queued[0]["record_id"], "title", "corrected", actor="reviewer")
        saved = state.rows("SELECT data_json, evidence_json FROM records")[0]
        audit = state.rows("SELECT field_name, actor FROM record_edits")[0]

        assert json.loads(saved["data_json"])["title"] == "corrected"
        assert json.loads(saved["evidence_json"])["_review"]["edits"][0]["old_value"] == "old"
        assert audit == {"field_name": "title", "actor": "reviewer"}


def test_conditional_cross_field_anomaly_and_persisted_rule_stats(tmp_path) -> None:
    fields = {
        "status": {"type": "enum", "values": ["open", "closed"]},
        "closed_at": {
            "type": "date",
            "required_if": {"field": "status", "equals": "closed"},
        },
        "minimum": {"type": "number"},
        "maximum": {"type": "number", "gte_field": "minimum", "anomaly": True, "anomaly_zscore": 1.5},
    }
    values = [10, 11, 10, 9, 100]
    records = [
        ExtractedRecord(
            f"https://example.org/{index}",
            "item",
            {"status": "closed", "minimum": 5, "maximum": value},
        )
        for index, value in enumerate(values)
    ]
    summary = assess_records(records, fields)
    assert summary["anomalies"] == 1
    assert records[-1].evidence["_quality"]["review_required"] is True
    assert "closed_at" in records[0].evidence["_quality"]["missing_required"]

    with StateStore(tmp_path / "state.sqlite3") as state:
        run_id = state.start_run("quality", "project.yaml")
        state.add_quality_stats(run_id, summary["field_stats"])
        state.add_quality_stats(run_id, summary["field_stats"])
        stats = {row["field_name"]: row for row in state.quality_stats(run_id)}
        assert stats["maximum"]["total"] == 10
        assert stats["maximum"]["anomalies"] == 2
        assert stats["maximum"]["completeness"] == 1.0
