from datetime import date

from omnicrawler.services.scenario_cards import REQUIRED, assess_card


def test_old_evidence_is_stale_without_claiming_template_broken(tmp_path):
    card = {"schema_version": 1, **dict.fromkeys(REQUIRED, "description"),
            "verification": "fixture_verified", "verified_at": "2026-01-01",
            "evidence": [{"path": "report.json", "sha256": "a" * 64}], "valid_for_days": 30}
    report = assess_card(card, today=date(2026, 10, 5))
    assert report["state"] == "stale"
    assert report["broken"] is False
    assert report["evidence_integrity"] == "not_checked"


def test_evidence_hash_mismatch_is_separate_from_age(tmp_path):
    card = {"schema_version": 1, **dict.fromkeys(REQUIRED, "description"),
            "verification": "fixture_verified", "verified_at": "2026-10-04",
            "evidence": [{"path": "report.json", "sha256": "a" * 64}]}
    (tmp_path / "report.json").write_text("different bytes")
    report = assess_card(card, root=tmp_path, today=date(2026, 10, 5))
    assert report["evidence_integrity"] == "mismatch"
    assert report["state"] == "evidence_invalid"
    assert report["broken"] is False
