"""三个市场页的搜索：**共通字段行为一致**，专有字段各自不同。

为什么要有这个文件：三处 docstring 都曾写「同口径」，容易被读成「字段集相同」。
实际是「**共通字段的行为一致**」：

| 页面 | 匹配字段 |
|---|---|
| 插件页 `_matches_filters` | id / name / category / summary / tags / **plugin_types** |
| 模板页 `_template_matches` | id / name / category / summary / tags |
| 组件页 `filter_components` | id / name / **publisher** / **hosts** |

差异来自数据模型不同（模板没有 plugin_types，组件没有 category/tags），
**不是缺陷**。但如果没人钉住「哪些是共通、哪些是专有」，
后人「统一」时很容易把某一页的专有字段硬塞进另一页。
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6.QtWidgets")

from omnicrawler.gui.views.component_market_logic import ComponentEntry, filter_components  # noqa: E402
from omnicrawler.gui.views.template_market import _template_matches  # noqa: E402


def _component(**overrides) -> ComponentEntry:
    base = dict(
        id="comp-1",
        name="示例组件",
        version="1.0.0",
        publisher="Acme Corp",
        description_file="",
        recipe_file="",
        recipe_digest="",
        declared_total_bytes=0,
        artifacts=[],
    )
    base.update(overrides)
    return ComponentEntry(**base)


# ── 共通字段：id / name 在两个页面都能搜到 ──────────────────────────────


@pytest.mark.parametrize("query", ["comp-1", "COMP-1"])
def test_component_matches_id_case_insensitively(query: str) -> None:
    assert filter_components([_component()], query)


@pytest.mark.parametrize("query", ["示例", "示例组件"])
def test_component_matches_name(query: str) -> None:
    assert filter_components([_component()], query)


@pytest.mark.parametrize("query", ["TPL-1", "tpl-1"])
def test_template_matches_id_case_insensitively(query: str) -> None:
    assert _template_matches({"id": "tpl-1", "name": "模板"}, query)


def test_both_pages_agree_on_id_and_name() -> None:
    """共通字段上，两个页面的判定必须一致 —— 这条防「某页漏了 casefold」。"""
    component = _component(id="shared-key", name="Shared Name")
    template = {"id": "shared-key", "name": "Shared Name"}
    for query in ("shared-key", "SHARED-KEY", "shared name", "SHARED NAME", "SHARE"):
        assert bool(filter_components([component], query)) == _template_matches(template, query), query


# ── 专有字段：只在各自的页面生效（记录差异，而非「修平」）──────────────


def test_publisher_is_component_only() -> None:
    """`publisher` 是组件专有字段 —— 模板条目没有这个概念。"""
    assert filter_components([_component(publisher="Acme Corp")], "Acme")
    assert not _template_matches({"id": "t", "name": "n"}, "Acme")


def test_empty_query_returns_everything() -> None:
    """空查询在两处都表示「不过滤」。"""
    assert len(filter_components([_component()], "")) == 1
    assert _template_matches({"id": "t"}, "")


def test_component_non_match_is_excluded() -> None:
    """不匹配的条目要被排除 —— 否则过滤等于没做。"""
    assert filter_components([_component()], "zzz-not-here") == []
