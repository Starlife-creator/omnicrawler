from __future__ import annotations

import json

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.templates.capture import capture, trial_reference
from omnicrawler.templates.template_catalog import bundled_template_catalog


def captured(tmp_path, source, **sections):
    path = tmp_path / "task.yaml"
    raw = {"project": {"name": "complex", "workspace": "work"}, "source": source,
           "extract": {"mode": "json", "item_path": "$.items[*]", "fields": {"id": {"path": "id"}}}, **sections}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    config = load_config(path)
    reference = trial_reference(config, {"status": "succeeded", "processed": 1, "records": 1},
                                 [{"request_fingerprint": "a" * 64, "content_sha256": "b" * 64}], [])
    proof = tmp_path / "proof.json"
    proof.write_text(json.dumps(reference), encoding="utf-8")
    output = tmp_path / "templates" / "complex.yaml"
    capture(config, proof, output, template_id="user/complex")
    return output, bundled_template_catalog([output.parent])


def test_cursor_json_download_semantics_survive_capture(tmp_path):
    output, catalog = captured(tmp_path, {"kind": "rest", "seeds": ["https://example.org?token=SECRET"],
        "pagination": {"type": "cursor", "next_path": "$.next", "parameter": "cursor"}},
        download={"enabled": True, "extensions": [".pdf"], "verified_pdf_manifest": True})
    assert "SECRET" not in output.read_text(encoding="utf-8")
    result = catalog.render("user/complex", {"seed_url_1": "https://example.org/items"})
    assert result["source"]["pagination"]["next_path"] == "$.next"
    assert result["extract"]["item_path"] == "$.items[*]"
    assert result["extract"]["fields"]["id"] == {"path": "id"}
    assert result["download"]["verified_pdf_manifest"] is True


def test_graphql_and_seed_payload_are_required_without_secret_defaults(tmp_path):
    output, catalog = captured(tmp_path, {"kind": "graphql", "seeds": ["https://example.org/graphql"],
        "query": "QUERY-SECRET", "variables": {"token": "BODY-SECRET"}, "headers": {"Authorization": "HEADER-SECRET"}})
    text = output.read_text(encoding="utf-8")
    assert all(secret not in text for secret in ("QUERY-SECRET", "BODY-SECRET", "HEADER-SECRET"))
    with pytest.raises(ValueError, match="Missing"):
        catalog.render("user/complex", {"seed_url_1": "https://example.org/graphql"})
    result = catalog.render("user/complex", {"seed_url_1": "https://example.org/graphql", "source_query": "{ items { id } }",
        "source_variables": {"keyword": "new"}, "source_headers": {"Authorization": "secret://new-token"}})
    assert result["source"]["variables"] == {"keyword": "new"}
    assert result["source"]["query"] == "{ items { id } }"


def test_browser_actions_and_session_reference_rebind_without_copying_values(tmp_path):
    output, catalog = captured(tmp_path, {"kind": "browser", "seeds": [{"url": "https://example.org", "method": "POST", "payload": {"token": "PAYLOAD-SECRET"}}]},
        browser={"actions": [{"action": "fill", "selector": "#query", "value": "ACTION-SECRET"}, {"action": "click", "selector": "#search"}]},
        session={"persist_cookies": True, "name": "ACCOUNT-SECRET"}, extract={"mode": "fields", "fields": {"title": "h1"}})
    assert all(secret not in output.read_text(encoding="utf-8") for secret in ("PAYLOAD-SECRET", "ACTION-SECRET", "ACCOUNT-SECRET"))
    result = catalog.render("user/complex", {"seed_url_1": "https://example.org/new", "seed_payload_1": {"term": "new"},
        "action_value_1": "new", "session_name": "chosen-local-session"})
    assert result["source"]["seeds"][0]["payload"] == {"term": "new"}
    assert result["browser"]["actions"][0]["value"] == "new"
    assert result["session"]["name"] == "chosen-local-session"


def test_pdf_processing_and_resource_settings_survive_capture(tmp_path):
    output, catalog = captured(tmp_path, {"kind": "static_html", "seeds": ["https://example.org/list"]},
        processors={"pdf": {"enabled": True, "config": "builtin:pdf/generic_template.yaml", "skip_ocr": True}},
        resources={"adaptive_concurrency": False, "maximum_process_tree_bytes": 100000000})
    result = catalog.render("user/complex", {"seed_url_1": "https://example.org/new"})
    assert result["processors"]["pdf"]["enabled"] is True
    assert result["processors"]["pdf"]["skip_ocr"] is True
    assert result["resources"] == {"adaptive_concurrency": False, "maximum_process_tree_bytes": 100000000}
    assert "pdf" in catalog.get("user/complex").metadata.capabilities
