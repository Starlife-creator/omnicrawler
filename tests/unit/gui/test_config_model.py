"""GUI 配置数据模型（CrawlConfig/FieldDef/DownloadConfig）纯逻辑测试。

无 Qt 依赖：模块仅用 dataclass + gettext，可直接在 CI 跑。
"""

from __future__ import annotations

from omnicrawler.gui.core.config_model import (
    CrawlConfig,
    DownloadConfig,
    FieldDef,
)


def _valid_field(**overrides) -> FieldDef:
    """构造一个默认合法的字段定义。"""
    base = {"name": "title", "selector": "h1.title"}
    base.update(overrides)
    return FieldDef(**base)


# ── FieldDef.validate ────────────────────────────────────────────


def test_field_def_valid_has_no_errors() -> None:
    assert _valid_field().validate() == []


def test_field_def_empty_name_reported() -> None:
    errors = _valid_field(name="  ").validate()
    assert any("字段名不能为空" in e for e in errors)


def test_field_def_empty_selector_reported() -> None:
    errors = _valid_field(selector="").validate()
    assert any("选择器不能为空" in e for e in errors)


def test_field_def_invalid_selector_type_reported() -> None:
    errors = _valid_field(selector_type="regex").validate()
    assert any("选择器类型无效" in e for e in errors)


def test_field_def_bad_regex_reported() -> None:
    errors = _valid_field(regex="(unclosed").validate()
    assert any("正则表达式无效" in e for e in errors)


# ── DownloadConfig.validate ──────────────────────────────────────


def test_download_disabled_by_default_no_errors() -> None:
    assert DownloadConfig().validate() == []


def test_download_enabled_without_extensions_reported() -> None:
    errors = DownloadConfig(enabled=True, extensions=[]).validate()
    assert any("未指定文件扩展名" in e for e in errors)


def test_download_enabled_without_output_dir_reported() -> None:
    errors = DownloadConfig(enabled=True, output_dir="  ").validate()
    assert any("未指定输出目录" in e for e in errors)


# ── CrawlConfig.validate ─────────────────────────────────────────


def _valid_config(**overrides) -> CrawlConfig:
    base = {"seed_urls": ["https://example.com"]}
    base.update(overrides)
    return CrawlConfig(**base)


def test_valid_config_passes() -> None:
    assert _valid_config().validate() == []


def test_missing_seed_url_reported() -> None:
    errors = CrawlConfig().validate()
    assert any("种子 URL" in e for e in errors)


def test_blank_seed_urls_only_do_not_count() -> None:
    errors = CrawlConfig(seed_urls=["", "   "]).validate()
    assert any("种子 URL" in e for e in errors)


def test_max_pages_must_be_positive() -> None:
    assert any("最大页数" in e for e in _valid_config(max_pages=0).validate())


def test_negative_delay_reported() -> None:
    assert any("延迟" in e for e in _valid_config(delay=-0.1).validate())


def test_concurrency_must_be_at_least_one() -> None:
    assert any("并发" in e for e in _valid_config(concurrency=0).validate())


def test_invalid_resource_profile_reported() -> None:
    assert any("资源模式" in e for e in _valid_config(resource_profile="turbo").validate())


def test_invalid_pdf_ocr_reported() -> None:
    assert any("OCR" in e for e in _valid_config(pdf_ocr="cloud").validate())


def test_ai_mode_requires_base_url_and_model() -> None:
    errors = _valid_config(ai_mode="cloud").validate()
    assert any("API 地址和模型名" in e for e in errors)


def test_ai_mode_with_base_url_and_model_passes() -> None:
    cfg = _valid_config(ai_mode="cloud", ai_base_url="https://api.example.com", ai_model="m1")
    assert cfg.validate() == []


def test_empty_output_formats_reported() -> None:
    errors = _valid_config(output_formats=[]).validate()
    assert any("输出格式" in e for e in errors)


def test_incremental_bad_since_date_reported() -> None:
    errors = _valid_config(incremental=True, since_date="2026/01/01").validate()
    assert any("YYYY-MM-DD" in e for e in errors)


def test_incremental_good_since_date_passes() -> None:
    cfg = _valid_config(incremental=True, since_date="2026-01-01")
    assert cfg.validate() == []


def test_duplicate_field_names_reported() -> None:
    cfg = _valid_config(fields=[_valid_field(name="a"), _valid_field(name="a")])
    assert any("字段名不能重复" in e for e in cfg.validate())


def test_is_valid_false_when_errors_exist() -> None:
    assert CrawlConfig().is_valid() is False
    assert _valid_config().is_valid() is True


# ── has_placeholders（B02-026：整棵配置树扫描） ────────────────────


def test_no_placeholder_by_default() -> None:
    assert _valid_config().has_placeholders() is False


def test_placeholder_in_seed_urls_detected() -> None:
    cfg = _valid_config(seed_urls=["https://{{domain}}.example.com"])
    assert cfg.has_placeholders() is True


def test_placeholder_in_deep_passthrough_detected() -> None:
    cfg = _valid_config(passthrough={"http": {"headers": {"X-Tag": "{{api_token}}"}}})
    assert cfg.has_placeholders() is True


def test_placeholder_in_field_regex_detected() -> None:
    cfg = _valid_config(fields=[_valid_field(regex="{{pattern}}")])
    assert cfg.has_placeholders() is True


# ── prune_orphan_overrides（B-2 孤儿键清理） ──────────────────────


def test_prune_orphan_overrides_removes_stale_urls() -> None:
    cfg = _valid_config(
        seed_urls=["https://a.com", "https://b.com"],
        per_url_template_overrides={
            "https://a.com": "tpl_a",
            "https://gone.com": "tpl_gone",
        },
    )
    removed = cfg.prune_orphan_overrides()
    assert removed == 1
    assert cfg.per_url_template_overrides == {"https://a.com": "tpl_a"}


def test_prune_orphan_overrides_noop_when_all_match() -> None:
    cfg = _valid_config(
        seed_urls=["https://a.com"],
        per_url_template_overrides={"https://a.com": "tpl_a"},
    )
    assert cfg.prune_orphan_overrides() == 0


# ── __post_init__ workspace 默认值 ───────────────────────────────


def test_workspace_default_derived_from_project_name() -> None:
    cfg = CrawlConfig(project_name="mytask", seed_urls=["https://example.com"])
    assert cfg.workspace == "work/mytask"


# ── 取值位置（W2.2 / §5.3 #9）：引擎支持"取元素自身"，GUI 不应再单方面卡住 ──


def test_field_def_element_position_needs_no_selector() -> None:
    """取条目元素自身文本：选择器为空是**合法**的（引擎行为如此）。"""
    from omnicrawler.core.field_value_source import POSITION_ELEMENT

    assert _valid_field(selector="", position=POSITION_ELEMENT).validate() == []


def test_field_def_element_attr_position_requires_attribute() -> None:
    """取条目元素自身属性：必须给属性名，否则会静默退化成"取自身文本"。"""
    from omnicrawler.core.field_value_source import POSITION_ELEMENT_ATTR

    ok = _valid_field(selector="", attribute="href", position=POSITION_ELEMENT_ATTR)
    assert ok.validate() == []
    missing = _valid_field(selector="", attribute=None, position=POSITION_ELEMENT_ATTR)
    assert any("属性名" in issue for issue in missing.validate())


def test_field_def_child_position_requires_selector() -> None:
    """显式选了"子元素"位置却没填选择器 ⇒ 报错（且提示指向位置选择）。"""
    from omnicrawler.core.field_value_source import POSITION_CHILD

    errors = _valid_field(selector="", position=POSITION_CHILD).validate()
    assert any("选择器为空" in issue for issue in errors), errors


def test_unset_position_with_empty_selector_is_not_silently_reinterpreted() -> None:
    """**位置未选 + 选择器为空** 必须报错：不许静默解释成"取元素自身"。

    这条守的是最容易出的事故：用户在表单里新加一行、忘了填选择器，
    若按形状推导就会"合法地"变成另一种语义（取整块元素的文本），结果静默取错值。
    """
    errors = _valid_field(selector="  ", position=None).validate()
    assert any("选择器不能为空" in issue for issue in errors), errors


def test_loaded_config_has_explicit_position() -> None:
    """从配置加载的字段位置是**显式**的 ⇒ 旧配置零迁移、且不会被上面的"未选"规则误伤。"""
    from omnicrawler.core.field_value_source import POSITION_CHILD, POSITION_ELEMENT_ATTR
    from omnicrawler.gui.core.config_serializer import from_yaml

    config = from_yaml(
        "project: {name: t, workspace: work/t}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        "extract:\n"
        "  mode: html\n"
        "  item_selector: a.item\n"
        "  fields:\n"
        "    title: {selector: h2.t}\n"
        "    link: {selector: '', attr: href}\n"
    )
    by_name = {f.name: f for f in config.fields}
    assert by_name["title"].position == POSITION_CHILD
    assert by_name["link"].position == POSITION_ELEMENT_ATTR
    assert by_name["link"].validate() == []
