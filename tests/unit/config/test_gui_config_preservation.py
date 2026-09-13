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
plugins:
  paths: [plugins/site.py, plugins_installed/]
  fail_open: true
  enabled_market_plugins: [demo]
  permission_grants:
    demo:
      version: 1.0.0
      artifact_sha256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
      creator: example-publisher
      permissions: [network]
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
    # 2026-09-13 补：本测试 fixture 原本就含 `extract: {mode: json, ...}`，
    # 却漏断言 mode —— 而 save_yaml 曾把它写死成 "html"，漏掉的正好是缺陷所在。
    assert loaded.passthrough["extract"]["mode"] == "json"
    assert loaded.passthrough["plugins"]["fail_open"] is True
    assert loaded.passthrough["plugins"]["paths"] == [
        "plugins/site.py",
        "plugins_installed/",
    ]
    assert loaded.passthrough["plugins"]["enabled_market_plugins"] == ["demo"]
    assert loaded.passthrough["plugins"]["permission_grants"]["demo"] == {
        "version": "1.0.0",
        "artifact_sha256": "a" * 64,
        "creator": "example-publisher",
        "permissions": ["network"],
    }


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


def test_gui_round_trip_keeps_unmodelled_extract_and_http_keys() -> None:
    """往返不得用硬编码默认值覆盖 B 类透传键。

    历史缺陷：`save_yaml` 把 `extract.mode` 写死 `"html"`、`extract.item_selector`
    写死 `""`、`http.auto_browser_fallback` 写死 `True`，而 `_deep_overlay` 让 root
    胜出 ⇒ **在 GUI 里打开一个可用的配置再运行，会被静默降级**：
    `item_selector` 变空（整页当成一条记录）、`mode` 变 html（JSON API 任务失效）。

    注意触发面：GUI 不是只在"另存为"时重写 YAML——`WorkerTaskRunner.start()`
    在把配置交给 worker 之前就调用 `save_yaml`，所以**每次运行都会发生**。
    """
    from omnicrawler.gui.core.config_serializer import from_yaml, to_yaml

    original = """
project: {name: demo, workspace: work/demo}
source: {kind: static_html, seeds: [https://example.org/list]}
http: {auto_browser_fallback: false}
extract:
  mode: html
  item_selector: div.item
  fields: {title: {selector: h1}}
"""
    config = from_yaml(original)
    loaded = from_yaml(to_yaml(config))

    assert loaded.passthrough["extract"]["item_selector"] == "div.item"
    assert loaded.passthrough["extract"]["mode"] == "html"
    assert loaded.passthrough["http"]["auto_browser_fallback"] is False
    assert "title" in loaded.passthrough["extract"]["fields"]


def test_gui_round_trip_does_not_invent_auto_browser_fallback_when_absent() -> None:
    """配置未声明 auto_browser_fallback 时，默认仍按应用默认（True）输出。

    这一条锁定"只在 passthrough 有显式值时才让原值胜出"的语义：
    缺失 ≠ 被覆盖成 False。
    """
    from omnicrawler.gui.core.config_serializer import from_yaml, to_yaml

    original = """
project: {name: demo, workspace: work/demo}
source: {kind: static_html, seeds: [https://example.org/list]}
extract: {mode: json, fields: {}}
"""
    loaded = from_yaml(to_yaml(from_yaml(original)))

    assert loaded.passthrough["http"]["auto_browser_fallback"] is True
    assert loaded.passthrough["extract"]["mode"] == "json"


def test_json_mode_fields_validate_and_keep_paths() -> None:
    """JSON 模式：GUI 不得用「选择器必填」卡住合法配置，也不得注入空 selector。

    历史缺陷：JSON 字段契约是 path / paths（见 extraction.jsonpath.json_field_values），
    而 GUI 模型只有 selector（未建模 path）⇒ 模型里 selector 恒空。两个后果：
    ① validate_full_config 直接拒绝，**合法 JSON 配置无法从 GUI 启动**；
    ② save_yaml 照样输出 selector 空串，往合法规则里塞无意义键。
    """
    from omnicrawler.gui.core.config_serializer import from_yaml, to_yaml
    from omnicrawler.gui.core.validator import validate_full_config

    json_config = from_yaml(
        '''project: {name: api, workspace: work/api}
source: {kind: rest, seeds: [https://example.org/items]}
extract:
  mode: json
  item_path: $.items[*]
  fields:
    id: {path: id}
    value: {path: value}
'''
    )
    errors, _warnings = validate_full_config(json_config)
    assert not [e for e in errors if "选择器" in e], f"JSON 模式不应要求选择器：{errors}"

    loaded = from_yaml(to_yaml(json_config))
    fields = loaded.passthrough["extract"]["fields"]
    assert fields["id"]["path"] == "id"
    assert "selector" not in fields["id"], "不应往 JSON 字段规则里注入空 selector"

    # 反向护栏：修 JSON 不能顺带放松 HTML 的「选择器必填」
    html_config = from_yaml(
        '''project: {name: h, workspace: work/h}
source: {kind: static_html, seeds: [https://example.org/]}
extract: {mode: html, fields: {title: {selector: ''}}}
'''
    )
    html_errors, _ = validate_full_config(html_config)
    assert any("选择器" in e for e in html_errors), "HTML 模式仍应要求选择器"
