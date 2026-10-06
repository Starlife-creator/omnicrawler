from datetime import UTC, datetime, timedelta

import pytest
import yaml

from omnicrawler.services.template_verification import verify_fixture
from omnicrawler.templates.template_catalog import TemplateCatalog, TemplateProbe
from omnicrawler.templates.verification import VerificationStore


def fixture_task(tmp_path):
    templates = tmp_path / "templates"
    templates.mkdir()
    raw = {"template": {"id": "fixture/demo", "name": "demo", "domains": ["example.test"],
                       "placeholders": {"seed_url": {"type": "string", "required": True},
                                        "item": {"type": "string", "default": ".row"}}},
           "project": {"name": "demo"}, "source": {"kind": "static_html", "seeds": ["{{seed_url}}"]},
           "extract": {"mode": "json", "item_path": "$.items[*]", "fields": {"price": {"path": "price"}}}}
    source = templates / "demo.yaml"
    source.write_text(yaml.safe_dump(raw), encoding="utf8")
    body, expected = tmp_path / "fixture.json", tmp_path / "truth.json"
    body.write_text('{"items":[{"price":0}]}', encoding="utf8")
    expected.write_text('[{"price":false}]', encoding="utf8")
    return TemplateCatalog(templates), source, raw, body, expected


def test_failed_fixture_quarantines_only_bound_scope_and_success_restores(tmp_path):
    catalog, source, raw, body, expected = fixture_task(tmp_path)
    probe = TemplateProbe("https://example.test/items")
    assert catalog.recommend(probe)
    result = verify_fixture(catalog, "fixture/demo", probe.url, body, expected)
    assert result["status"] == "failed" and result["verification"] == "unverified"  # False is not numeric zero.
    assert catalog.recommend(probe) == []
    assert catalog.recommend(TemplateProbe("https://example.test/other"))
    raw["template"]["version"] = "1.0.1"
    source.write_text(yaml.safe_dump(raw), encoding="utf8")
    catalog.discover(refresh=True)
    assert catalog.recommend(probe)
    verify_fixture(catalog, "fixture/demo", probe.url, body, expected)
    assert catalog.recommend(probe) == []
    expected.write_text('[{"price":0}]', encoding="utf8")
    result = verify_fixture(catalog, "fixture/demo", probe.url, body, expected)
    assert result["ok"] and result["network_accessed"] is False
    assert catalog.recommend(probe)
    history = VerificationStore().history("fixture/demo")
    assert [row["status"] for row in history] == ["passed", "failed", "failed"]
    assert history[0]["expected_sha256"] == history[0]["actual_sha256"]


def test_custom_parameter_failure_cannot_poison_default_recommendation(tmp_path):
    catalog, _, _, body, expected = fixture_task(tmp_path)
    result = verify_fixture(catalog, "fixture/demo", "https://example.test/items", body, expected, values={"item": ".custom"})
    assert not result["ok"]
    assert catalog.recommend(TemplateProbe("https://example.test/items"))


def test_expired_success_needs_recheck_without_claiming_broken(tmp_path):
    catalog, _, _, body, expected = fixture_task(tmp_path)
    expected.write_text('[{"price":0}]', encoding="utf8")
    result = verify_fixture(catalog, "fixture/demo", "https://example.test/items", body, expected)
    result["checked_at"] = (datetime.now(UTC) - timedelta(days=100)).isoformat()
    VerificationStore().record(result)
    matches = catalog.recommend(TemplateProbe("https://example.test/items"))
    assert matches and "fixture:recheck_due" in matches[0].reasons


def test_invalid_config_stays_inspectable_but_is_not_recommended(tmp_path):
    catalog, source, raw, _, _ = fixture_task(tmp_path)
    raw["extract"]["fields"]["price"] = {"selector": "wrong JSON key"}
    source.write_text(yaml.safe_dump(raw), encoding="utf8")
    catalog.discover(refresh=True)
    assert catalog.get("fixture/demo") is not None
    assert catalog.recommend(TemplateProbe("https://example.test/items")) == []


def test_empty_truth_does_not_create_vacuous_verification(tmp_path):
    catalog, _, _, body, expected = fixture_task(tmp_path)
    expected.write_text("[]", encoding="utf8")
    with pytest.raises(ValueError, match="真值"):
        verify_fixture(catalog, "fixture/demo", "https://example.test/items", body, expected)
    assert VerificationStore().history("fixture/demo") == []
    assert catalog.recommend(TemplateProbe("https://example.test/items"))
