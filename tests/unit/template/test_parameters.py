from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from omnicrawler.commands import template
from omnicrawler.services.config_history import ConfigHistory
from omnicrawler.templates.parameters import validate_parameters
from omnicrawler.templates.template_catalog import TemplateCatalog, bundled_template_catalog
from omnicrawler.templates.template_health import validate_template


def test_cli_text_values_become_declared_types():
    rendered = bundled_template_catalog().render("generic/numbered-pagination", {
        "seed_url": "https://example.test/", "end_page": "7",
    })
    assert rendered["source"]["pagination"]["end"] == 7
    assert type(rendered["source"]["pagination"]["end"]) is int


@pytest.mark.parametrize("value", [True, 0, 1001, "oops", "NaN", 1.5])
def test_pagination_rejects_invalid_parameters(value):
    with pytest.raises(ValueError):
        bundled_template_catalog().render("generic/numbered-pagination", {
            "seed_url": "https://example.test/", "end_page": value,
        })


def test_missing_required_declaration_not_referenced_still_rejects():
    with pytest.raises(ValueError, match="required"):
        validate_parameters({"approval": {"required": True}}, {}, strict=True)
    assert validate_parameters({"approval": {"required": True}}, {}, strict=False) == {"approval": None}


def test_preview_does_not_bypass_constraints_or_leak_supplied_values():
    with pytest.raises(ValueError) as error:
        validate_parameters({"limit": {"type": "integer", "maximum": 10}},
                            {"limit": "sensitive-placeholder-value"}, strict=False)
    assert "sensitive-placeholder-value" not in str(error.value)


def test_boolean_is_not_integer_and_enum_keeps_type_identity():
    with pytest.raises(ValueError):
        validate_parameters({"limit": {"enum": [1]}}, {"limit": True}, strict=True)
    assert validate_parameters({"enabled": {"type": "boolean"}}, {"enabled": "false"}, strict=True)["enabled"] is False


def _catalog(tmp_path: Path, *, valid: bool) -> TemplateCatalog:
    directory = tmp_path / "templates"
    directory.mkdir()
    raw = {"template": {"id": "example", "description": "example", "version": "1.0.0"},
           "project": {"name": "example"}, "source": {"kind": "static_html", "seeds": ["https://example.test/"]},
           "crawl": {"max_pages": 5 if valid else -1}}
    (directory / "example.yaml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    return TemplateCatalog(directory)


def test_failed_render_preserves_existing_task_and_removes_temporary_file(tmp_path, monkeypatch):
    catalog = _catalog(tmp_path, valid=False)
    monkeypatch.setattr(template, "bundled_template_catalog", lambda *_: catalog)
    output = tmp_path / "task.yaml"
    original = b"project: {name: preserved}\r\n"
    output.write_bytes(original)
    with pytest.raises(ValueError):
        template.execute("render", template_id="example", output=str(output), force=True)
    assert output.read_bytes() == original
    assert list(tmp_path.glob("*.yaml")) == [output]


def test_successful_overwrite_retains_restorable_config_history(tmp_path, monkeypatch):
    catalog = _catalog(tmp_path, valid=True)
    monkeypatch.setattr(template, "bundled_template_catalog", lambda *_: catalog)
    output = tmp_path / "task.yaml"
    original = b"project: {name: preserved}\r\n"
    output.write_bytes(original)
    template.execute("render", template_id="example", output=str(output), force=True)
    history = ConfigHistory(tmp_path / ".config_history")
    versions = history.list("task")
    assert versions and versions[0]["reason"] == "before_template_render"
    history.restore(Path(versions[0]["path"]), tmp_path / "restored.yaml")
    assert (tmp_path / "restored.yaml").read_bytes() == original


def test_catalog_validation_rejects_invalid_default(tmp_path):
    catalog = _catalog(tmp_path, valid=True)
    record = catalog.get("example")
    assert record is not None
    record.metadata.placeholders["limit"] = {"type": "integer", "default": -1, "minimum": 1}
    health = validate_template(record)
    assert not health.ok
    assert any("range" in error for error in health.errors)
