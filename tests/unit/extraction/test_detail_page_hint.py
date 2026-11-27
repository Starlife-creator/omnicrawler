"""错误信息必须**指向真正的原因**：这是详情页，不是列表页。

## 事故形态（2026-11-27 实测）

`scrapingcourse.com/ecommerce/product/abominable-hoodie/` 是一个**商品详情页**，
但分析器把重复元素选到了面包屑上：

    item_selector = ... > div.storefront-breadcrumb
    字段填充情况 = 链接文本(1), 价格(0), 标题(0), 分类(0), 图片地址(0)

页框判据正确拦住了它，但**错误信息把人引向错误方向**：

    「先确认目标 URL 就是列表页（商品列表常在 /shop、/products 等路径下…）」

而用户给的**就是**一个商品页 —— 它本来就不该按列表抓。

## 判据（三条同时成立）

1. 列表项很少（`<= 6`）；
2. 填上的**业务字段**不超过 1 个 ——
   `内容_<tag>` 与 `链接文本/链接地址/图片地址` 都不算业务字段；
3. 页面**在框架之外**有足够正文（`>= 500` 字符）。

★ 第 2 条**不能写成「全部为 0」**：实测该页的 `链接文本` 恰好填了 1 条
  （面包屑里那个链接），「全 0」判据会漏掉 —— 而那 1 条正是"只抓到了导航"。

★ 第 3 条**不可省**，而且**不能用「有没有 `product-main` 块」代替**：
  我第一版就是这么写的，实测真实站点用的是 woocommerce 自己的类名，
  那个判据认不出来 ⇒ 提示永不触发。改用「框架外正文占比」这个更通用的形态判据：

  | 页面 | 框架外正文 | 结论 |
  |---|---|---|
  | 商品详情页（面包屑被当列表）| 8534 字符 | 详情页 ✓ |
  | 只有侧边栏的页面 | 0 字符 | 不是详情页 ✓ |
"""

from __future__ import annotations

import pytest

from omnicrawler.extraction.intelligent_scraper import (
    AutoConfigUnverifiedError,
    _body_text_beyond_chrome,
    _is_business_field_name,
    _looks_like_detail_page,
    analyze_to_config,
)

# ── 字段名判据 ──────────────────────────────────────────────────────────


@pytest.mark.parametrize("name", ["标题", "价格", "库存状态", "作者", "评分"])
def test_business_field_names_are_recognised(name: str) -> None:
    assert _is_business_field_name(name) is True


@pytest.mark.parametrize(
    "name",
    ["内容_span", "内容_td_2", "链接文本", "链接地址", "图片地址", "", "   "],
)
def test_fallback_and_navigation_names_are_not_business(name: str) -> None:
    """★ 两类「看着有值但不是业务」的字段名：兜底名 + 导航/链接名。"""
    assert _is_business_field_name(name) is False


# ── 三判据 ──────────────────────────────────────────────────────────────


def _detail_page_html() -> str:
    """复现真实形态：详情页里唯一在重复的是**面包屑**，但主体有大量正文。"""
    crumbs = "".join(f'<a href="/c{i}">C{i}</a>' for i in range(5))
    description = (
        "This hoodie is made from a heavyweight cotton blend and features a "
        "double-lined hood, kangaroo pocket and ribbed cuffs. Machine washable. "
    ) * 6  # 与真实商品描述同量级
    return "".join([
        "<html><body>",
        f'<div class="storefront-breadcrumb"><nav>{crumbs}</nav></div>',
        '<div class="summary">',
        "<h1>Abominable Hoodie</h1>",
        '<span class="price">$59.00</span>',
        f'<p class="description">{description}</p>',
        "</div>",
        "</body></html>",
    ])


def test_detail_page_error_mentions_detail_hint() -> None:
    """★ 这条就是事故：详情页被拒时，提示必须说「这像详情页」。"""
    html = _detail_page_html()
    with pytest.raises(AutoConfigUnverifiedError) as exc:
        analyze_to_config(html, "https://example.org/product/1")
    message = str(exc.value)
    assert "详情页" in message, message


def test_plain_sidebar_page_does_not_get_the_detail_hint() -> None:
    """★ 反方向：只有侧边栏的页面**不该**被叫成详情页。

    它框架外几乎没有正文 —— 那是「页面上没内容」，不是「详情页被误当列表」。
    """
    html = (
        '<html><body><aside class="sidebar">'
        + "".join(f'<a href="/x{i}">L{i}</a>' for i in range(5))
        + "</aside></body></html>"
    )
    with pytest.raises(AutoConfigUnverifiedError) as exc:
        analyze_to_config(html, "https://example.org/list")
    message = str(exc.value)
    assert "页面框架" in message
    assert "详情页" not in message, message


def test_body_text_helper_separates_the_two_shapes() -> None:
    """★ 第 3 条判据本身：详情页有大段正文，侧边栏页没有。"""
    assert _body_text_beyond_chrome(_detail_page_html()) >= 500
    sidebar = '<html><body><aside class="sidebar"><a>A</a><a>B</a></aside></body></html>'
    assert _body_text_beyond_chrome(sidebar) < 500


def test_many_items_are_never_treated_as_detail() -> None:
    """列表项一多就不可能是详情页（即使字段填得很差）。"""
    report = {"fields": {"链接文本": 20}}
    assert _looks_like_detail_page(report, 20, _detail_page_html()) is False
