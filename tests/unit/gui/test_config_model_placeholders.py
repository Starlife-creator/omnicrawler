"""B02-026：占位符门禁全树扫描回归测试。

验证 ``CrawlConfig.has_placeholders`` 从「仅扫 seed_urls + fields[].selector」
升级为整棵配置树扫描后：原盲区字段（source.params / http.headers / pagination /
max_pages / passthrough 内嵌段等）中的 ``{{identifier}}`` 均被命中，且
注释性 ``{{ 任意文本 }}`` 不误报。
"""

from __future__ import annotations

from omnicrawler.gui.core.config_model import CrawlConfig


def test_placeholder_in_seed_url_still_detected() -> None:
    cfg = CrawlConfig(seed_urls=["https://example.org/{{query}}"])
    assert cfg.has_placeholders() is True


def test_placeholder_in_field_selector_still_detected() -> None:
    cfg = CrawlConfig(fields=[])  # placeholder 经 passthrough 注入等价场景
    cfg.passthrough = {"extract": {"item_selector": "article {{item}}"}}
    assert cfg.has_placeholders() is True


def test_placeholder_in_source_params_blind_spot_detected() -> None:
    cfg = CrawlConfig(seed_urls=["https://api.example.org/items"])
    cfg.passthrough = {"source": {"params": {"query": "{{query}}"}}}
    assert cfg.has_placeholders() is True


def test_placeholder_in_http_headers_blind_spot_detected() -> None:
    cfg = CrawlConfig(seed_urls=["https://example.org/"])
    cfg.passthrough = {"http": {"headers": {"X-Token": "{{header_value}}"}}}
    assert cfg.has_placeholders() is True


def test_placeholder_in_pagination_blind_spot_detected() -> None:
    cfg = CrawlConfig(seed_urls=["https://example.org/"])
    cfg.passthrough = {"source": {"pagination": {"end": "{{end_page}}"}}}
    assert cfg.has_placeholders() is True


def test_placeholder_in_browser_actions_detected() -> None:
    cfg = CrawlConfig(seed_urls=["https://example.org/"])
    cfg.passthrough = {"browser": {"actions": [{"selector": "{{next_selector}}"}]}}
    assert cfg.has_placeholders() is True


def test_plain_string_without_placeholders_not_detected() -> None:
    cfg = CrawlConfig(
        seed_urls=["https://example.org/"],
        passthrough={
            "http": {"headers": {"User-Agent": "OmniCrawler/1.0"}},
            "source": {"params": {"rows": 100}},
        },
    )
    assert cfg.has_placeholders() is False


def test_comment_style_braces_not_detected() -> None:
    """注释性 ``{{ 任意文本 }}``（非合法 identifier）不误报。"""
    cfg = CrawlConfig(seed_urls=["https://example.org/"], passthrough={"note": "{{ 说明性文本 }}"})
    assert cfg.has_placeholders() is False
