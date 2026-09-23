"""内置 JSON 模板字段契约守卫（B0 复现 + B1 门禁的用例层）。

字段契约（明文）：JSON 模式的字段规则键是 ``path`` / ``paths`` ——
``extraction/extractors.py::json_field_values`` 只读这两个键；
``gui/core/config_model.py`` 的 ``FieldDef.validate`` 文档同样写明。
``selector`` 是 HTML 模式的键，在 JSON 模式是**死键**：路径会静默回退成
字段名，``doi: {selector: DOI}`` 取不到 ``DOI``。

历史缺陷（2026-09-23 审计）：7 个内置 JSON 模板 section 级用了已废弃的
``item_selector``（加载期会被 ``core/migrations.py`` 自动迁移，因此表面正常），
字段级却全部用了 ``selector``（**无任何迁移覆盖**，产生确定性错值）。
另注意：``json_path`` 的数组展开需要 ``[*]`` 后缀（如 ``message.items[*]``），
裸 ``message.items`` 会把整个数组当成**一条**记录。

本文件钉三件事（每条都可反向触发）：
1. 迁移行为：``load_config`` 会迁移 section 级 ``item_selector``，
   但**不会**迁移字段级 ``selector`` —— 证明缺陷不在 section 级；
2. 对照实验：同一 crossref fixture 下，selector 写法必须塌缩成
   「1 条空记录」，path 写法必须逐条命中 —— 把「死键」钉成可推翻的判据；
3. 全量静态断言：所有 builtin ``mode: json`` 模板不得含 ``item_selector``，
   字段必须用 ``path``/``paths``；catalog 容错后 validate 仍必须 fail-closed。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.extraction.extractors import JSONProcessor
from omnicrawler.templates import template_catalog as template_catalog_module
from omnicrawler.templates.template_catalog import TemplateCatalog
from omnicrawler.templates.template_health import validate_catalog

# ── crossref 形态的最小 fixture（键名大小写与真实响应一致）──────────────

_CROSSREF_ITEM = {
    "DOI": "10.1234/abc",
    "title": ["A Title"],
    "type": "journal-article",
    "published": {"date-parts": [[2024, 1, 15]]},
    "URL": "https://doi.org/10.1234/abc",
    "license": [{"URL": "https://creativecommons.org/licenses/by/4.0/"}],
}
_CROSSREF_BODY = json.dumps(
    {"status": "ok", "message": {"items": [_CROSSREF_ITEM, dict(_CROSSREF_ITEM, DOI="10.1234/def")]}},
    ensure_ascii=False,
).encode("utf-8")

_FIELDS_SELECTOR_FORM = """\
  mode: json
  item_selector: message.items
  fields:
    doi: {selector: DOI, type: jsonpath, required: true}
    title: {selector: title.0, type: jsonpath, required: true}
    published: {selector: published.date-parts.0, type: jsonpath}
    url: {selector: URL, type: jsonpath}
"""

_FIELDS_PATH_FORM = """\
  mode: json
  item_path: message.items[*]
  fields:
    doi: {path: DOI, type: jsonpath, required: true}
    title: {path: title.0, type: jsonpath, required: true}
    published: {path: published.date-parts.0, type: jsonpath}
    url: {path: URL, type: jsonpath}
"""


def _write_probe_config(tmp_path: Path, extract_block: str) -> Path:
    path = tmp_path / "probe.yaml"
    path.write_text(
        "project: {name: contract_probe, workspace: work/contract_probe}\n"
        "source: {kind: rest, seeds: ['https://api.example.org/v1/works']}\n"
        "extract:\n"
        f"{extract_block}",
        encoding="utf-8",
    )
    return path


def _process(config_path: Path):
    config = load_config(config_path)
    request = CrawlRequest("https://api.example.org/v1/works?query=test")
    result = FetchResult(
        request, request.url, 200, {"content-type": "application/json"}, _CROSSREF_BODY, 0.1,
    )
    return JSONProcessor(config).process(result).records


# ── 1. 迁移行为：section 级有兜底，字段级没有 ─────────────────────────

def test_load_config_migrates_item_selector_but_not_field_selector(tmp_path: Path) -> None:
    config = load_config(_write_probe_config(tmp_path, _FIELDS_SELECTOR_FORM))
    extract = config.section("extract")
    # section 级：加载期自动迁移（这正是它"表面正常"的原因）。
    # 注：DEFAULTS 里的空默认 `item_selector: ""` 会被 deep_merge 带回，
    # 因此判据是「用户值不再存在」，而不是「键不存在」。
    assert extract.get("item_path") == "message.items"
    assert not extract.get("item_selector"), "用户写的 item_selector 应已被迁移删除"
    # 字段级：无任何迁移覆盖 —— 缺陷本体在这里
    doi_rule = extract["fields"]["doi"]
    assert doi_rule.get("selector") == "DOI"
    assert "path" not in doi_rule and "paths" not in doi_rule


# ── 2. 对照实验：selector 塌缩 vs path 全命中 ─────────────────────────

def test_selector_form_collapses_to_single_empty_record(tmp_path: Path) -> None:
    """selector 写法的确定性后果：2 条 items 只产出 1 条记录、字段全空。

    反向对照见 test_path_form_extracts_every_field —— 两者共用同一 fixture。
    """
    records = _process(_write_probe_config(tmp_path, _FIELDS_SELECTOR_FORM))
    assert len(records) == 1, "裸数组路径未带 [*]，整个 items 数组被当成一条记录"
    assert records[0].data == {}, f"字段应全部取空，实际: {records[0].data}"


def test_path_form_extracts_every_field(tmp_path: Path) -> None:
    records = _process(_write_probe_config(tmp_path, _FIELDS_PATH_FORM))
    assert len(records) == 2
    assert [item.data["doi"] for item in records] == ["10.1234/abc", "10.1234/def"]
    assert all(item.data["title"] == "A Title" for item in records)
    assert records[0].data["url"] == "https://doi.org/10.1234/abc"
    assert records[0].data["published"] == [2024, 1, 15]


# ── 3. 全量静态断言（守卫本体）───────────────────────────────────────

def _json_mode_records():
    catalog = bundled_catalog()
    return [
        record
        for record in catalog.discover()
        if isinstance(record.config.get("extract"), dict)
        and record.config["extract"].get("mode") == "json"
    ]


def bundled_catalog() -> TemplateCatalog:
    return TemplateCatalog(Path(template_catalog_module.__file__).parent)


def test_builtin_json_templates_do_not_use_deprecated_item_selector() -> None:
    offenders = {
        record.metadata.template_id: record.config["extract"]["item_selector"]
        for record in _json_mode_records()
        if "item_selector" in record.config["extract"]
    }
    assert not offenders, (
        "JSON 模式的 item_selector 已废弃（加载期迁移掩盖了它），应改写 item_path："
        f"{offenders}"
    )


def test_builtin_json_fields_use_path_contract() -> None:
    offenders: dict[str, list[str]] = {}
    for record in _json_mode_records():
        fields = record.config["extract"].get("fields") or {}
        bad = [
            name
            for name, rule in fields.items()
            if isinstance(rule, dict)
            and "selector" in rule
            and not ({"path", "paths"} & rule.keys())
        ]
        if bad:
            offenders[record.metadata.template_id] = sorted(bad)
    assert not offenders, (
        "JSON 字段契约是 path/paths（json_field_values 从不读 selector），"
        f"以下模板字段取不到值：{offenders}"
    )


# ── 4. catalog 容错 + fail-closed（B1 的 discover 改动配套）───────────

def test_catalog_reports_parse_errors_without_losing_builtin_templates(tmp_path: Path) -> None:
    user_dir = tmp_path / "user_templates"
    user_dir.mkdir()
    (user_dir / "broken.yaml").write_text("template: [unclosed\n", encoding="utf-8")
    catalog = TemplateCatalog(Path(template_catalog_module.__file__).parent, (user_dir,))

    records = catalog.discover()
    # 2026-09-23 迁市场后内核 41 套：用"必需 id 集合"代替魔法阈值（B3 口径，此处补齐）。
    ids = {r.metadata.template_id for r in records}
    required = {"generic/list-detail", "generic/single-page", "protocols/rest-offset",
                "industries/government-policy", "documents/pdf-collection"}
    missing = required - ids
    assert not missing, f"一个坏用户模板不得让内置模板消失（缺：{sorted(missing)}）"
    assert catalog.parse_errors, "坏文件必须被记录，而不是静默消失"


def test_validate_catalog_fails_closed_on_parse_errors(tmp_path: Path) -> None:
    user_dir = tmp_path / "user_templates"
    user_dir.mkdir()
    (user_dir / "broken.yaml").write_text("template: [unclosed\n", encoding="utf-8")
    catalog = TemplateCatalog(Path(template_catalog_module.__file__).parent, (user_dir,))

    health = validate_catalog(catalog)
    assert not all(item.ok for item in health), "容错 ≠ 静默放行：破损文件必须让校验失败"
    assert any("broken" in message for item in health for message in item.errors)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
