"""GUI 配置校验器（validate_schema/validate_selector_format/validate_full_config）测试。

无 Qt 依赖：纯逻辑校验，可直接在 CI 跑。
"""

from __future__ import annotations

from omnicrawler.gui.core.config_model import CrawlConfig, FieldDef
from omnicrawler.gui.core.validator import (
    validate_full_config,
    validate_schema,
    validate_selector_format,
)


def _field(**overrides) -> FieldDef:
    base = {"name": "title", "selector": "h1.title"}
    base.update(overrides)
    return FieldDef(**base)


def _schema_dict(**overrides) -> dict:
    """构造一份默认合法的 Schema 字典。"""
    base = {
        "project": {"name": "demo", "workspace": "work/demo"},
        "source": {"kind": "static_html", "seeds": ["https://example.com"]},
        "extract": {"fields": {"title": {"selector": "h1", "type": "css"}}},
    }
    base.update(overrides)
    return base


# ── validate_selector_format ─────────────────────────────────────


def test_valid_css_selector_passes() -> None:
    assert validate_selector_format(_field()) == []


def test_xpath_selector_flagged_when_type_is_css() -> None:
    errors = validate_selector_format(_field(selector="//div[@id='main']"))
    assert any("XPath" in e for e in errors)


def test_jsonpath_selector_flagged_when_type_is_css() -> None:
    errors = validate_selector_format(_field(selector="$.data.items[0].title"))
    assert any("JSONPath" in e for e in errors)


def test_css_selector_flagged_when_type_is_xpath() -> None:
    errors = validate_selector_format(_field(selector=".content", selector_type="xpath"))
    assert any("XPath" in e for e in errors)


def test_jsonpath_must_start_with_dollar() -> None:
    errors = validate_selector_format(_field(selector="data.items", selector_type="jsonpath"))
    assert any("$" in e for e in errors)


def test_empty_selector_reported() -> None:
    errors = validate_selector_format(_field(selector="   "))
    assert any("选择器为空" in e for e in errors)


# ── validate_schema ──────────────────────────────────────────────


def test_valid_schema_no_errors_no_warnings() -> None:
    errors, warnings = validate_schema(_schema_dict())
    assert errors == []
    assert warnings == []


def test_unknown_top_key_reported() -> None:
    errors, _ = validate_schema(_schema_dict(unknown_section={}))
    assert any("未知的顶层配置项" in e for e in errors)


def test_missing_project_name_reported() -> None:
    errors, _ = validate_schema(_schema_dict(project={"workspace": "w"}))
    assert any("project.name" in e for e in errors)


def test_missing_workspace_warns_only() -> None:
    _, warnings = validate_schema(_schema_dict(project={"name": "demo"}))
    assert any("workspace" in w for w in warnings)


def test_missing_source_kind_reported() -> None:
    errors, _ = validate_schema(_schema_dict(source={"seeds": ["https://example.com"]}))
    assert any("source.kind" in e for e in errors)


def test_unsupported_source_kind_reported() -> None:
    errors, _ = validate_schema(_schema_dict(source={"kind": "teleport", "seeds": []}))
    assert any("不支持的 source.kind" in e for e in errors)


def test_empty_seeds_reported_except_redis_scrapy() -> None:
    errors, _ = validate_schema(
        _schema_dict(source={"kind": "static_html", "seeds": []})
    )
    assert any("seeds" in e for e in errors)


def test_empty_seeds_allowed_for_redis() -> None:
    errors, _ = validate_schema(_schema_dict(source={"kind": "redis", "seeds": []}))
    assert errors == []


def test_extra_source_kinds_extend_whitelist() -> None:
    errors, _ = validate_schema(
        _schema_dict(source={"kind": "custom_plugin", "seeds": ["redis://srv"]}),
        extra_source_kinds={"custom_plugin"},
    )
    assert errors == []


def test_empty_extract_fields_warns_auto_extraction() -> None:
    _, warnings = validate_schema(_schema_dict(extract={"fields": {}}))
    assert any("自动提取" in w for w in warnings)


# ── validate_full_config ─────────────────────────────────────────


def _valid_config(**overrides) -> CrawlConfig:
    base = {"seed_urls": ["https://example.com"]}
    base.update(overrides)
    return CrawlConfig(**base)


def test_full_config_valid_passes() -> None:
    errors, _ = validate_full_config(_valid_config())
    assert errors == []


def test_full_config_invalid_source_kind_reported() -> None:
    errors, _ = validate_full_config(_valid_config(source_kind="not_a_kind"))
    assert any("网站类型" in e for e in errors)


def test_full_config_propagates_field_selector_errors() -> None:
    cfg = _valid_config(fields=[_field(selector="//div", selector_type="css")])
    errors, _ = validate_full_config(cfg)
    assert any("XPath" in e for e in errors)


def test_full_config_placeholder_warns() -> None:
    cfg = _valid_config(seed_urls=["https://{{domain}}.example.com"])
    _, warnings = validate_full_config(cfg)
    assert any("占位符" in w for w in warnings)
