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
    from omnicrawler.gui.core.config_serializer import from_yaml, to_yaml

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


def test_prune_orphan_overrides_removes_stale_urls() -> None:
    """P2-5b：seed_urls 变更后，孤儿覆盖键被清理。"""
    from omnicrawler.gui.core.config_model import CrawlConfig

    config = CrawlConfig()
    config.seed_urls = ["https://a.example/list", "https://b.example/list"]
    config.per_url_template_overrides = {
        "https://a.example/list": "generic/html-table",
        "https://removed.example/list": "generic/list-detail",
        "https://renamed.example/list": "generic/single-page",
    }
    pruned = config.prune_orphan_overrides()
    assert pruned == 2
    assert config.per_url_template_overrides == {
        "https://a.example/list": "generic/html-table"
    }
    # 再次调用无孤儿，返回 0
    assert config.prune_orphan_overrides() == 0


def test_to_yaml_prunes_orphan_overrides_before_serialize() -> None:
    """P2-5b：序列化边界自动清理孤儿覆盖键（保存/运行前生效）。"""
    from omnicrawler.gui.core.config_model import CrawlConfig
    from omnicrawler.gui.core.config_serializer import to_yaml

    config = CrawlConfig()
    config.seed_urls = ["https://a.example/list"]
    config.per_url_template_overrides = {
        "https://a.example/list": "generic/html-table",
        "https://stale.example/list": "generic/single-page",
    }
    yaml_str = to_yaml(config)
    assert "https://a.example/list" in yaml_str
    assert "https://stale.example/list" not in yaml_str
    assert config.per_url_template_overrides == {"https://a.example/list": "generic/html-table"}
