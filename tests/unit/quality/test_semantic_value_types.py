"""Semantic changes retain the distinction between JSON booleans and numbers."""
import pytest

from omnicrawler.quality.semantic_changes import compare_record_data


@pytest.mark.parametrize(("before", "after"), [
    (False, 0), (True, 1), (0, False),
    ({"in_stock": False}, {"in_stock": 0}), ([False], [0]),
    ({"rows": [{"flag": True}]}, {"rows": [{"flag": 1}]}),
])
def test_boolean_numeric_type_changes_are_visible(before, after):
    change = compare_record_data({"value": before}, {"value": after})
    assert change.change_type == "modified"
    assert change.modified_fields == ("value",)


@pytest.mark.parametrize(("before", "after"), [(1, 1.0), (" A  B ", "A B"), ([1], [1.0])])
def test_existing_numeric_and_whitespace_normalization_remains_stable(before, after):
    assert compare_record_data({"value": before}, {"value": after}).change_type == "unchanged"
