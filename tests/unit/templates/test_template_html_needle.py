"""`html_contains` 的匹配判据：词按整词、标记片段按子串。

## 事故形态（2026-11-27 实测）

w3schools 的 **HTML 表格教程页**上，`templates inspect` 把
`industries/ecommerce-products`（电商商品）排在第一位（30 分），
而真正对的 `generic/html-table` 只有 15 分。

原因：`html_contains: [Product, offers, priceCurrency]` 里的 `Product` 按**子串**匹配，
在该页命中 53 次 —— 但作为**整词**命中 **0** 次，全部落在
`products` / `production` / `ecommerce-products` 这类更长的词里。

## 判据

| 条目形态 | 匹配方式 | 为什么 |
|---|---|---|
| `<table` / `<picture`（以 `<` 开头）| 子串 | 标记片段没有词边界 |
| `Product` / `offers` / `NewsArticle` | **整词** | 它们来自 JSON-LD 的 `@type`/键名，当散文子串匹配必然误报 |

★ 关键在于**不能丢掉真阳性**：真实电商页（web-scraping.dev / scrapingcourse /
  scrapeme.live）的 `product` 整词命中分别是 20 / 245 / 71，
  所以它们照旧被识别出来。
"""

from __future__ import annotations

import pytest

from omnicrawler.templates.template_catalog import _html_needle_hit


@pytest.mark.parametrize(
    "needle",
    ["<table", "<picture", "<video"],
)
def test_markup_fragments_use_substring_matching(needle: str) -> None:
    """标记片段后面紧跟 `>` 或空白，没有词边界 —— 必须走子串。"""
    assert _html_needle_hit(needle, f"<html><body>{needle} class=\"x\"></body></html>".casefold())


def test_markup_fragment_absent_is_a_miss() -> None:
    assert not _html_needle_hit("<table", "<html><body><div>no table here</div></body></html>")


def test_word_matches_a_json_ld_type() -> None:
    """真实信号形态：JSON-LD 的 `"@type": "Product"`。引号不是文字字符 ⇒ 命中。"""
    body = '{"@context":"https://schema.org","@type":"Product","name":"x"}'.casefold()
    assert _html_needle_hit("Product", body) is True


def test_word_does_not_match_a_longer_word() -> None:
    """★ 这条就是事故本身：`products` 里含 `product`，但不是商品结构化数据。"""
    assert _html_needle_hit("Product", "our products and production line") is False


def test_word_does_not_match_a_hyphenated_slug() -> None:
    """`ecommerce-products` 这类 slug 里的 product 也不是结构化数据。"""
    body = '<a href="/category/ecommerce-products">x</a>'
    assert _html_needle_hit("Product", body.casefold()) is False


def test_word_matches_case_insensitively() -> None:
    assert _html_needle_hit("NewsArticle", '{"@type":"newsarticle"}') is True


def test_word_at_string_boundary_matches() -> None:
    """首尾就是边界的情形（`Product` 单独成串）。"""
    assert _html_needle_hit("Product", "product") is True


def test_real_ecommerce_pages_still_match_product() -> None:
    """★ 反方向：不能因为收紧判据，把真实电商页也判掉。

    这里用的是**实测计数**（2026-11-27 抓到的真站）：

        web-scraping.dev/products       product 整词 20
        scrapingcourse.com/ecommerce/   product 整词 245
        scrapeme.live/shop/             product 整词 71

    判据不联网：用同等形态的样本证「整词能命中」。
    """
    real_shape = '<div class="product" data-type="Product"><h2>Product name</h2></div>'.casefold()
    assert _html_needle_hit("Product", real_shape) is True
