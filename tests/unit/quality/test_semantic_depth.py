from dataclasses import replace

import pytest

from omnicrawler.core.models import ExtractedRecord
from omnicrawler.quality.quality import assess_record, assess_records
from omnicrawler.quality.temporal_facts import EntityRegistry, TemporalFact, infer_business_event


@pytest.mark.parametrize("value,event", [("2026-10-07", "unchanged"), ("2026-10-06", "advanced"), ("2026-10-08", "postponed"), ("unknown", "field_changed")])
def test_date_events_follow_actual_direction_without_invented_probability(value, event):
    before = TemporalFact("id", "date", "2026-10-07", "2026-01-01", "2026-10-01", "https://a", "e1")
    after = replace(before, value=value, evidence_id="e2")
    result = infer_business_event(before, after)
    assert result.event_type == event
    assert result.confidence is None


def test_amount_equivalence_requires_explicit_matching_units_and_currency():
    before = TemporalFact("id", "amount", 100, "now", "now", "https://a", "e1", unit="万元", currency="CNY")
    after = replace(before, value=1000000, unit="元")
    assert infer_business_event(before, after).event_type == "unchanged"
    assert infer_business_event(before, replace(after, currency="USD")).event_type == "amount_changed"
    assert infer_business_event(before, replace(after, unit=None)).event_type == "amount_changed"
    with pytest.raises(ValueError, match="相同实体"):
        infer_business_event(before, replace(after, entity_id="other"))


def test_failed_entity_merge_does_not_corrupt_registry():
    registry = EntityRegistry()
    registry.merge("a", "b")
    with pytest.raises(ValueError, match="循环"):
        registry.merge("b", "a")
    assert registry.resolve("a") == "b" and registry.resolve("b") == "b"


def test_valid_contract_does_not_hide_wrong_record_evidence_or_weak_key_field():
    record = ExtractedRecord("https://a", "item", {"amount": 12}, {
        "amount": {"matches": 1, "source_url": "https://a", "clean_value": 99, "confidence": 0.2}})
    quality = assess_record(record, {"amount": {"type": "number", "critical": True, "min_confidence": 0.8}})
    assert quality["score"] == 1
    assert quality["score_semantics"] == "contract_compliance_v1"
    assert quality["dimensions"]["contract"] == "passed"
    assert quality["evidence_status"]["amount"] == "value_mismatch"
    assert quality["review_required"] and len(quality["evidence_issues"]) == 2


def test_grouped_robust_anomalies_report_cold_start_and_resist_nonfinite_values():
    records = [ExtractedRecord("https://a", "item", {"category": "A", "price": value}) for value in [10, 10, 10, 10, 1000]]
    records += [ExtractedRecord("https://a", "item", {"category": "B", "price": 99999})]
    records += [ExtractedRecord("https://a", "item", {"category": "A", "price": float("nan")})]
    result = assess_records(records, {"price": {"type": "number", "anomaly": True, "anomaly_group_by": ["category"]}})
    assert result["anomalies"] == 1
    assert records[4].evidence["_quality"]["anomalies"][0]["method"] == "mad"
    assert records[5].evidence["_quality"]["anomaly_assessment"]["price"] == "insufficient_samples"
    assert records[6].evidence["_quality"]["review_required"]
