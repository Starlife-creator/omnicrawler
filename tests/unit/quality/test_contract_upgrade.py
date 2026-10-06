from dataclasses import replace

import pytest

from omnicrawler.quality.schema_registry import DatasetContract, FieldContract, analyse_contract_change


@pytest.mark.parametrize("old,new", [
    ({}, {"minimum": 0}),
    ({"maximum": 100}, {"maximum": 50}),
    ({"nullable": True}, {"nullable": False}),
    ({"allow_empty": True}, {"allow_empty": False}),
    ({}, {"min_length": 1}),
    ({}, {"unique": True}),
    ({"data_type": "enum", "enum": ("a", "b")}, {"data_type": "enum", "enum": ("a",)}),
    ({"data_type": "array"}, {"data_type": "array", "items": {"type": "number"}}),
    ({"data_type": "object", "properties": {}}, {"data_type": "object", "properties": {"id": {"type": "text", "required": True}}}),
])
def test_tightening_requires_historical_review(old, new):
    field = FieldContract("value", "number", "Business value")
    before = DatasetContract("items", "1", (replace(field, **old),))
    after = DatasetContract("items", "2", (replace(field, **new),))
    impact = analyse_contract_change(before, after)
    assert impact.compatibility == "migration_required"
    assert impact.constraint_changes == ("value",)
    assert impact.historical_reprocess_required


def test_loosening_preserves_compatibility():
    field = FieldContract("value", "number", "Business value", minimum=1, nullable=False)
    before = DatasetContract("items", "1", (field,))
    after = replace(before, version="2", fields=(replace(field, minimum=0, nullable=True),))
    assert analyse_contract_change(before, after).compatibility == "compatible"


def test_meaning_changes_are_breaking_even_with_same_json_type():
    field = FieldContract("value", "number", "CNY amount")
    before = DatasetContract("items", "1", (field,))
    after = replace(before, version="2", fields=(replace(field, meaning="USD amount"),))
    impact = analyse_contract_change(before, after)
    assert impact.compatibility == "breaking"
    assert impact.meaning_changes == ("value",)
