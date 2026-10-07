from types import SimpleNamespace

import pytest

from omnicrawler.core.models import ExtractedRecord
from omnicrawler.fetching.browser_engines import BrowserAction, SeleniumAdapter
from omnicrawler.quality.quality import assess_record


@pytest.mark.parametrize("value", ["abc123", "12万元", "NaN", "Infinity", True, "12,34"])
def test_numeric_contract_rejects_ambiguous_values_without_mutating(value):
    record = ExtractedRecord("https://example.org", "item", {"amount": value})
    result = assess_record(record, {"amount": {"type": "number", "required": True}})
    assert result["review_required"]
    assert result["validation_errors"]
    assert record.data["amount"] == value


@pytest.mark.parametrize("value", ["1e3", "1,000", "1000.01"])
def test_numeric_bounds_compare_actual_value(value):
    record = ExtractedRecord("https://example.org", "item", {"amount": value})
    assert assess_record(record, {"amount": {"type": "number", "max": 100}})["review_required"]


def test_decimal_bounds_do_not_round_money():
    record = ExtractedRecord("https://example.org", "item", {"amount": "9007199254740993"})
    assert assess_record(record, {"amount": {"type": "number", "max": "9007199254740992"}})["review_required"]


def test_money_validation_preserves_existing_currency_support_and_units():
    record = ExtractedRecord("https://example.org", "item", {"amount": "12万元"})
    assert assess_record(record, {"amount": {"type": "money", "max": 1000}})["review_required"]
    record.data["amount"] = "CNY 20"
    assert not assess_record(record, {"amount": {"type": "money", "max": 1000}})["review_required"]


def test_cross_field_comparison_keeps_decimal_precision():
    record = ExtractedRecord("https://example.org", "item", {"a": "9007199254740993", "b": "9007199254740992"})
    assert not assess_record(record, {"a": {"type": "number", "gt_field": "b"}})["review_required"]


def test_legacy_selenium_role_name_matches_the_accessible_name():
    pytest.importorskip("selenium")
    wrong = SimpleNamespace(accessible_name="Cancel", text="Cancel")
    right = SimpleNamespace(accessible_name="Submit", text="Submit")
    driver = SimpleNamespace(find_elements=lambda *_: [wrong, right])
    action = BrowserAction.from_dict({"action": "click", "role": "button", "name": "Submit"})
    assert SeleniumAdapter(driver).locate(action) is right
