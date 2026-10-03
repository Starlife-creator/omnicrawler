from omnicrawler.templates.evidence import template_binding
from omnicrawler.templates.template_catalog import TemplateMetadata, TemplateRecord


def test_template_binding_changes_with_version_content_and_parameters(tmp_path):
    def bind(version="1", selector="h1", parameter="secret-value"):
        record = TemplateRecord(TemplateMetadata("custom", "Custom", "test", version=version),
                                tmp_path / "task.yaml", {"extract": {"selector": selector}})
        return template_binding(record, {"token": parameter})

    current = bind()
    assert current["historical_reference_only"] is True
    assert "secret-value" not in str(current)
    assert bind(version="2")["template_version"] != current["template_version"]
    assert bind(selector="h2")["template_sha256"] != current["template_sha256"]
    assert bind(parameter="another")["parameters_sha256"] != current["parameters_sha256"]
