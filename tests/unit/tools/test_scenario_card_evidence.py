from __future__ import annotations

import hashlib
import json

import pytest

from omnicrawler.services.scenario_cards import REQUIRED
from tools.check_scenario_cards import check


def test_card_checker_verifies_bytes_and_rejects_empty_or_outside_selection(tmp_path):
    evidence = tmp_path / "result.json"
    evidence.write_text("verified sample", encoding="utf-8")
    card = {"schema_version": 1, **dict.fromkeys(REQUIRED, "fixture"), "verification": "fixture_verified", "verified_at": "2026-10-04", "evidence": [{"path": evidence.name, "sha256": hashlib.sha256(evidence.read_bytes()).hexdigest()}]}
    path = tmp_path / "card.json"
    path.write_text(json.dumps(card), encoding="utf-8")
    assert check([path], tmp_path)[0]["verification"] == "fixture_verified"
    evidence.write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        check([path], tmp_path)
    with pytest.raises(ValueError, match="zero"):
        check([], tmp_path)
    card["evidence"][0]["path"] = "../outside.json"
    path.write_text(json.dumps(card), encoding="utf-8")
    with pytest.raises(ValueError, match="inside"):
        check([path], tmp_path)
