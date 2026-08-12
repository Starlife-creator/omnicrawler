"""S3.3.2：配置往返 e2e + 结构化证据路径参数化测试。

本测试覆盖 B 类（passthrough 透传）字段的往返保活——即 GUI 不编辑
但 AppConfig 需要的字段（plugins、session、processors 等）在
CrawlConfig → YAML → CrawlConfig 往返后不丢失。

A 类（GUI 可编辑）字段的显式映射契约见 test_field_mapping_contract.py。
"""

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
