from __future__ import annotations

from pathlib import Path

import yaml

from omnicrawler.templates.template_catalog import TemplateCatalog, TemplateProbe, bundled_template_catalog


def test_bundled_catalog_is_recursive_and_searchable() -> None:
    catalog = bundled_template_catalog()
    records = catalog.discover()
    identifiers = {record.metadata.template_id for record in records}

    # B3：魔法阈值（>=30）改为"必需模板 id 集合"断言（可反向触发）。
    _required_templates = {
        "generic/list-detail",
        "generic/single-page",
        "protocols/rest-offset",
        "sites/crossref-works",
        "cms/wordpress-rest",
        "social/zhihu-topic",
        "industries/government-policy",
        "documents/pdf-collection",
        "authenticated/form-login",
        "recipes/dynamic-topic-pdf-monitor",
    }
    missing = _required_templates - identifiers
    assert not missing, f"必需模板缺失：{sorted(missing)}"
    assert "generic/list-detail" in identifiers
    assert "cms/wordpress-rest" in identifiers
    assert "industries/government-policy" in identifiers
    assert catalog.search("WordPress", category="cms")
    assert catalog.search(tags=["PDF"], capabilities=["ocr"])


def test_template_recommendation_uses_multiple_evidence_types() -> None:
    catalog = bundled_template_catalog()
    matches = catalog.recommend(
        TemplateProbe(
            "https://example.org/wp-json/wp/v2/posts",
            {"Content-Type": "application/json", "X-WP-TotalPages": "3"},
            '<link href="/wp-content/theme.css"><script>wp-json</script>',
            [{"id": 1, "title": {"rendered": "Hello"}, "content": {"rendered": "World"}}],
        )
    )

    assert matches
    assert matches[0].record.metadata.template_id == "cms/wordpress-rest"
    assert matches[0].score >= 75
    assert len(matches[0].reasons) >= 3


def test_render_is_deep_typed_and_strict() -> None:
    catalog = bundled_template_catalog()
    rendered = catalog.render(
        "generic/numbered-pagination",
        {"seed_url": "https://example.org/list", "end_page": 7},
    )

    assert "template" not in rendered
    assert rendered["source"]["seeds"] == ["https://example.org/list"]
    assert rendered["source"]["pagination"]["end"] == 7


def test_user_template_overrides_builtin_by_stable_id(tmp_path: Path) -> None:
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    base = {
        "template": {"id": "generic/demo", "name": "Built in"},
        "project": {"name": "demo"},
        "source": {"kind": "static_html", "seeds": ["https://example.org"]},
    }
    override = {**base, "template": {"id": "generic/demo", "name": "User override"}}
    (builtin / "demo.yaml").write_text(yaml.safe_dump(base), encoding="utf-8")
    (user / "demo.yaml").write_text(yaml.safe_dump(override), encoding="utf-8")

    record = TemplateCatalog(builtin, [user]).get("generic/demo")

    assert record is not None
    assert record.metadata.name == "User override"
    assert record.builtin is False


def test_builtin_escape_still_resolves_after_user_override(tmp_path: Path) -> None:
    """B02-010：内置模板被用户/市场同 id 覆盖后，`builtin:` 逃生仍应取到内置源真值。"""
    builtin = tmp_path / "builtin"
    user = tmp_path / "user"
    builtin.mkdir()
    user.mkdir()
    base = {
        "template": {"id": "sites/crossref-works", "name": "Built in"},
        "project": {"name": "demo"},
        "source": {"kind": "static_html", "seeds": ["https://example.org"]},
    }
    override = {**base, "template": {"id": "sites/crossref-works", "name": "Market override"}}
    (builtin / "sites").mkdir(parents=True, exist_ok=True)
    (builtin / "sites" / "crossref_works.yaml").write_text(yaml.safe_dump(base), encoding="utf-8")
    (user / "crossref.yaml").write_text(yaml.safe_dump(override), encoding="utf-8")

    catalog = TemplateCatalog(builtin, [user])
    assert catalog.get("sites/crossref-works").metadata.name == "Market override"  # 覆盖仍生效
    escape = catalog.get("builtin:sites/crossref_works.yaml")
    assert escape is not None
    assert escape.metadata.name == "Built in"  # 逃生取回内置真值
    assert escape.builtin is True


def test_bundled_templates_are_encoding_clean() -> None:
    """内置模板必须编码干净：无 U+FFFD，且每个 template 块显式声明 version。

    背景：历史上一次 GBK/UTF-8 往返把 25 个内置模板的 name/description 中文写成
    U+FFFD，并把 description 与 version 挤到同一行，导致 version 被静默回落到默认值。
    本测试作为回归护栏，阻止该类损坏再次进入仓库。
    """
    catalog = bundled_template_catalog()
    records = catalog.discover()
    assert records

    replacement_char_issues: list[str] = []
    missing_version: list[str] = []
    for record in records:
        relative = record.path.name
        if "\ufffd" in record.path.read_text(encoding="utf-8"):
            replacement_char_issues.append(relative)
        block = record.config.get("template")
        if isinstance(block, dict) and "version" not in block:
            missing_version.append(relative)

    assert not replacement_char_issues, (
        "内置模板含替换字符 U+FFFD（编码往返损坏）：" + ", ".join(replacement_char_issues)
    )
    assert not missing_version, (
        "template 块缺少显式 version 键（会被静默回落到默认 1.0.0）："
        + ", ".join(missing_version)
    )


def test_readme_template_count_matches_catalog() -> None:
    """B3：README 的模板数必须与 catalog 实际记录数一致（防文档漂移）。

    可反向触发：把 README 的"模板库（N 套）"改成任意别的数字即红。
    """
    import re

    readme = Path(__file__).resolve().parents[3] / "README.md"
    count = len(bundled_template_catalog().discover())
    matches = re.findall(r"模板库（(\d+) 套）", readme.read_text(encoding="utf-8"))
    assert matches, "README 模板库计数标记丢失"
    assert int(matches[0]) == count, f"README 模板数 {matches[0]} != catalog 实际 {count}"
