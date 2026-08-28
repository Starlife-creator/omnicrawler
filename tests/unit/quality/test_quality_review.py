from __future__ import annotations

import json

import pytest

from omnicrawler.core.models import CrawlRequest, ExtractedRecord
from omnicrawler.quality.quality import assess_record, assess_records
from omnicrawler.state import StateStore


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


def test_pattern_rule_rejects_redos_pattern() -> None:
    """B06-002：pattern 匹配必须走 safe_regex_search，嵌套量词类 ReDoS 模式被拒绝而非卡死。"""
    fields = {"title": {"pattern": r"(a+)+$"}}
    record = ExtractedRecord("https://example.org/1", "item", {"title": "a" * 100})
    quality = assess_record(record, fields)
    # 拒绝后可重试或判未匹配，但绝不能抛出/卡死；此处断言被标记为不符合 pattern
    assert any("does not match pattern" in err for err in quality["validation_errors"])


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


def test_review_queue_filters_by_run_and_honors_limit(tmp_path) -> None:
    with StateStore(tmp_path / "state.sqlite3") as state:
        first_run = state.start_run("first", "project.yaml")
        second_run = state.start_run("second", "project.yaml")
        for run_id, suffix in ((first_run, "a"), (first_run, "b"), (second_run, "c")):
            request = CrawlRequest(f"https://example.org/{suffix}")
            state.save_records(
                run_id,
                request,
                [
                    ExtractedRecord(
                        request.url,
                        "item",
                        {"title": suffix},
                        {"_quality": {"review_required": True, "score": 0.4}},
                    )
                ],
            )

        assert len(state.review_queue(first_run)) == 2
        assert len(state.review_queue(first_run, limit=1)) == 1
        assert len(state.review_queue()) == 3


def test_review_queue_applies_limit_after_exact_quality_filter(tmp_path) -> None:
    with StateStore(tmp_path / "state.sqlite3") as state:
        run_id = state.start_run("exact-review", "project.yaml")
        decoy_request = CrawlRequest("https://example.org/decoy")
        state.save_records(
            run_id,
            decoy_request,
            [
                ExtractedRecord(
                    decoy_request.url,
                    "item",
                    {"title": "decoy"},
                    {
                        "metadata": {"review_required": True},
                        "_quality": {"review_required": False},
                    },
                )
            ],
        )
        real_request = CrawlRequest("https://example.org/real")
        state.save_records(
            run_id,
            real_request,
            [
                ExtractedRecord(
                    real_request.url,
                    "item",
                    {"title": "real"},
                    {"_quality": {"review_required": True}},
                )
            ],
        )

        queue = state.review_queue(run_id, limit=1)
        assert [item["source_url"] for item in queue] == [real_request.url]
        assert state.review_queue(run_id, limit=0) == []
        with pytest.raises(ValueError, match="negative"):
            state.review_queue(run_id, limit=-1)


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
