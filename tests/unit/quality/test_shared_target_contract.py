import pytest

from omnicrawler.core.models import ExtractedRecord
from omnicrawler.extraction.ai_graph import AIGraphExtractor, FieldDef
from omnicrawler.quality.quality import assess_record
from omnicrawler.services.ai_safety import validate_target_fields


def test_optional_non_nullable_cannot_hide_null():
    with pytest.raises(ValueError):
        validate_target_fields({"price": None}, {"price": {"type": "number", "nullable": False}})


def test_required_nullable_is_present_and_empty_array_can_be_valid():
    assert validate_target_fields({"note": None, "items": []}, {
        "note": {"type": "text", "required": True, "nullable": True},
        "items": {"type": "list", "required": True, "allow_empty": True},
    }) == []


def test_nested_array_elements_reject_boolean_numbers():
    rule = {"items": {"type": "list", "items": {"type": "object", "properties": {
        "price": {"type": "number", "required": True},
    }}}}
    with pytest.raises(ValueError):
        validate_target_fields({"items": [{"price": True}]}, rule)
    assert validate_target_fields({"items": [{"price": 0}]}, rule) == []


def test_quality_strict_json_uses_the_same_native_types():
    record = ExtractedRecord("https://example.com", "item", {"price": "12"})
    quality = assess_record(record, {"price": {"type": "number", "strict_json": True}})
    assert quality["review_required"]
    assert quality["validation_errors"]
    assert not assess_record(record, {"price": {"type": "number"}})["review_required"]


def test_declared_empty_values_survive_graph_merge():
    extractor = AIGraphExtractor()
    fields = [FieldDef("items", required=True, field_type="list", allow_empty=True)]
    result = extractor._merge_results([{"fields": {"items": []}, "confidence": 1}], 1, fields=fields)
    extractor._assess_target(result, fields)
    assert result["fields"] == {"items": []}
    assert not result["review_required"]


def test_empty_valid_values_are_complete_in_quality_and_merge():
    record = ExtractedRecord("https://example.com", "item", {"note": None, "items": []})
    quality = assess_record(record, {
        "note": {"type": "text", "required": True, "nullable": True, "strict_json": True},
        "items": {"type": "list", "required": True, "allow_empty": True, "strict_json": True},
    })
    assert quality["completeness"] == 1
    assert not quality["review_required"]


def test_boolean_and_zero_in_nested_candidates_are_conflicting():
    result = AIGraphExtractor()._merge_results([
        {"fields": {"items": [0]}}, {"fields": {"items": [False]}},
    ], 2)
    assert result["conflicts"][0]["later"] == [False]
    assert result["field_sources"]["items"] == [0, 1]
