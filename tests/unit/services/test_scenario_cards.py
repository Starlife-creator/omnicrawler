from __future__ import annotations

import pytest

from omnicrawler.services.scenario_cards import REQUIRED, describe_card, validate_card


def test_verified_claim_requires_bounded_evidence():
    card = {"schema_version": 1, **dict.fromkeys(REQUIRED, "fixture"), "verification": "fixture_verified", "verified_at": "2026-10-04"}
    with pytest.raises(ValueError, match="evidence"):
        validate_card(card)
    card["evidence"] = [{"path": "evidence/report.json", "sha256": "a" * 64}]
    assert validate_card(card)["verification"] == "fixture_verified"
    card["evidence"][0]["sha256"] = "z" * 64
    with pytest.raises(ValueError, match="digest"):
        validate_card(card)


def test_missing_and_invalid_metadata_cannot_claim_verified():
    assert "未提供" in describe_card({})
    assert "格式无效" in describe_card({"scenario_card": {"schema_version": 1}})
    card = {"schema_version": 1, **dict.fromkeys(REQUIRED, "fixture")}
    assert "未经验证" in describe_card({"scenario_card": card})
    assert "不替代签名" in describe_card({"scenario_card": card})
