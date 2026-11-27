"""模板 `html_contains` 的**穷举守卫**：从真实模板目录枚举每一个标记，逐个验判据。

## 背景

`html_contains` 是模板匹配的主要打分来源（每命中 +15，最多 +50）。

2026-11-27 实测：`Product` 按**子串**匹配时，在一个 HTML 表格教程页上命中 53 次，
而作为**整词**命中 **0 次**（全部落在 `products` / `production` / `ecommerce-products` 里）
—— 于是「电商商品」（30 分）压过真正对的 `generic/html-table`（15 分）。

## ★ 一条踩过的坑：判据不能写成「散文里出现就不许命中」

第一版这个文件就是这么写的，结果 **41 条全红** —— 而且**红得对**：

    "This tutorial explains Product in detail."

里 `Product` 是**独立单词**，与 `<div class="Product">` 在字节上无从区分。
对一条基于文本的启发式打分器，「散文里提到这个词」本来就是弱信号之一，
它和「页面结构里有这个词」本来就不是可分辨的两件事。

真正可分辨、也正是本次修的那条是：**「不匹配更长的词」**。

## 判据（穷举两个方向）

| 方向 | 断言 |
|---|---|
| 词标记 + 后缀成更长的词（`products` / `productions`）| 不命中 |
| 词标记 + 词边界（独立词 / class 取值 / JSON-LD / `_` `-` 分隔）| 命中 |
| 标记片段（`<table` / `<rss`…）| 子串命中 |
"""

from __future__ import annotations

import pytest

from omnicrawler.templates.template_catalog import (
    _html_needle_hit,
    bundled_template_catalog,
)


def _all_template_markers() -> list[tuple[str, str]]:
    """`(模板 id, 标记)`，从**真实模板目录**枚举，不是手写清单。"""
    catalog = bundled_template_catalog()
    pairs: list[tuple[str, str]] = []
    for record in catalog.discover(None):
        metadata = record.metadata
        for marker in metadata.html_contains:
            pairs.append((metadata.template_id, str(marker)))
    return pairs


_MARKERS = _all_template_markers()
_WORD_MARKERS = [(tid, m) for tid, m in _MARKERS if not m.startswith("<")]
_FRAGMENT_MARKERS = [(tid, m) for tid, m in _MARKERS if m.startswith("<")]


def test_marker_set_is_not_empty() -> None:
    """★ 空集合会让下面所有参数化断言恒真。"""
    assert len(_MARKERS) >= 30, f"枚举到的标记太少，疑似模板目录没加载：{len(_MARKERS)}"
    assert len(_WORD_MARKERS) >= 20, len(_WORD_MARKERS)
    assert len(_FRAGMENT_MARKERS) >= 5, len(_FRAGMENT_MARKERS)
    assert len({tid for tid, _ in _MARKERS}) >= 10


@pytest.mark.parametrize(("template_id", "marker"), _WORD_MARKERS)
def test_word_marker_does_not_match_a_longer_word(template_id: str, marker: str) -> None:
    """★ 穷举：词标记**不得**匹配「它是前缀的更长的词」。

    这条就是 w3schools 事故的判据本身，只是从"只测 Product"扩到"测全部词标记"。
    新增模板/新增标记会自动被覆盖。
    """
    lowered = marker.casefold()
    for suffix in ("s", "es", "ing", "ed"):
        body = f"<html><body><p>see {lowered}{suffix} here</p></body></html>"
        assert not _html_needle_hit(marker, body), (
            f"模板 {template_id} 的标记 {marker!r} 匹配了更长的词 {lowered + suffix!r}"
        )


@pytest.mark.parametrize(("template_id", "marker"), _WORD_MARKERS)
def test_word_marker_matches_on_a_word_boundary(template_id: str, marker: str) -> None:
    """★ 反方向穷举：词标记作为独立词 / class 取值时必须命中。

    没有这条，上面那条可以用「一律不命中」通过（等于把功能删了）。
    """
    lowered = marker.casefold()
    for body in (
        f"<html><body><p>{lowered}</p></body></html>",
        f'<html><body><div class="{lowered}-item">x</div></body></html>',
        f'<html><body><div class="{lowered}">x</div></body></html>',
        f'<html><body><script>{{"@type":"{lowered}"}}</script></body></html>',
    ):
        assert _html_needle_hit(marker, body.casefold()), (
            f"模板 {template_id} 的标记 {marker!r} 在词边界上却没命中：{body}"
        )


@pytest.mark.parametrize(("template_id", "marker"), _FRAGMENT_MARKERS)
def test_tag_fragment_matches_by_substring(template_id: str, marker: str) -> None:
    """标记片段没有词边界，必须走子串（`<table` 后跟 `>` 或空白都算）。"""
    lowered = marker.casefold()
    for body in (f"<html><body>{lowered}>", f"<html><body>{lowered} class=\"x\">"):
        assert _html_needle_hit(marker, body.casefold()), (
            f"模板 {template_id} 的片段 {marker!r} 未命中：{body}"
        )
