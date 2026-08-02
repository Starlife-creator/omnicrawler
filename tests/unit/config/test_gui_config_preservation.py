from __future__ import annotations

import importlib.util

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("ruamel") is None,
    reason="GUI YAML round-trip requires the optional ruamel.yaml dependency",
)


def test_gui_round_trip_preserves_advanced_template_sections() -> None:
    from omnicrawl.gui.core.config_serializer import from_yaml, to_yaml

    original = """
template: {id: cms/example, version: 1.2.0}
project: {name: demo, workspace: work/demo, description: "采集政策附件"}
source:
  kind: site_wordpress
  seeds: [https://example.org/wp-json/wp/v2/posts]
  max_pages: 40
http:
  delay_seconds: 2
  headers: {Accept: application/json}
session: {persist_cookies: false, name: isolated}
plugins: {paths: [plugins/site.py], fail_open: true}
processors: {pdf: {enabled: false, config: configs/pdf/generic_template.yaml}}
extract: {mode: json, fields: {}}
"""
    config = from_yaml(original)
    config.project_name = "edited"
    rendered = to_yaml(config)
    loaded = from_yaml(rendered)

    assert loaded.project_name == "edited"
    assert loaded.task_description == "采集政策附件"
    assert loaded.source_kind == "site_wordpress"
    assert loaded.passthrough["source"]["max_pages"] == 40
    assert loaded.passthrough["http"]["headers"]["Accept"] == "application/json"
    assert loaded.passthrough["session"]["name"] == "isolated"
    assert loaded.passthrough["plugins"]["fail_open"] is True
