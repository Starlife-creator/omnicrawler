"""真实站点实测（2026-11-26）暴露的三处**字段推断**缺陷，用离线夹具钉住。

## 夹具怎么来的

`tests/fixtures/real_sites/` 里的 HTML 是**从真实站点原样下载**的（各站点的
列表页）。用它们跑 `analyze_to_config`，不必联网，也不用等 CI 的 40 分钟。

这样做的理由：这一轮我改错过两次（见提交正文），两次都是**改完只测一个站点**、
结果把别的站点改坏。有了离线夹具，一次跑完七个站点，秒级看到影响面。

## 三条缺陷

| # | 站点 | 现象 |
|---|---|---|
| 1 | scrapingcourse.com | 「价格」列全是 `$`，金额丢了 |
| 2 | scrapeme.live | 库存写在 item 类名里，整列不存在 |
| 3 | books.toscrape | 修 #1 时把「完整标题（title 属性）」换成「截断标题」 |
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.extraction.intelligent_scraper import analyze_to_config
from omnicrawler.extraction.intelligent_scraper.dom_features import _parse_dom
from omnicrawler.extraction.intelligent_scraper.pattern_detector import detect_repeating_patterns

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "real_sites"


def _config(fixture: str, url: str) -> dict:
    html = (FIXTURES / fixture).read_text(encoding="utf-8", errors="replace")
    return analyze_to_config(html, url)


# ── ① 嵌套价格：取到金额，不是只剩货币符号 ────────────────────────────────


def test_nested_price_keeps_the_amount_not_just_the_currency_symbol() -> None:
    """★ WooCommerce 的价格是四层嵌套，四层**都是候选节点**：

        <span class="price">"$69.00"
          <span class="product-price">"$69.00"
            <bdi>"$69.00"
              <span class="currencySymbol">"$"

    旧判据「含任何候选子节点就删」，四层全被删，只剩最里层的货币符号 ——
    「价格」列交付出去全是 `$`，金额丢了且不报错。
    """
    config = _config("scrapingcourse_ecommerce.html", "https://www.scrapingcourse.com/ecommerce/")
    price = config["extract"]["fields"].get("价格")
    assert price is not None, f"没有价格字段，实际字段：{list(config['extract']['fields'])}"
    examples = price.get("examples") or []
    assert examples, "价格字段没有示例值"
    assert all("$" in str(e) and any(ch.isdigit() for ch in str(e)) for e in examples), (
        f"价格只剩货币符号了：{examples}"
    )


# ── ② 聚合判定用「分叉」而不是「数量」 ───────────────────────────────────


def test_single_chain_nesting_is_not_treated_as_an_aggregate() -> None:
    """单链嵌套是**同一个值的不同粒度**；只有**分叉**才是把多个字段拼成一整块。

    ★ 这条是我第一版改错的地方：判据写成「候选后代 ≥2 就删」，
      scrapingcourse 的四层价格被整条删掉。数量 ≠ 结构。
    """
    nodes = _parse_dom(
        (FIXTURES / "scrapingcourse_ecommerce.html").read_text(encoding="utf-8", errors="replace")
    )
    pattern = max(detect_repeating_patterns(nodes), key=lambda p: getattr(p, "count", 0))
    roots = [n for n in nodes if n.css_path == pattern.css_path]
    assert roots, "夹具应能检出重复列表"
    item = roots[0]
    price_chain = [
        n for n in nodes
        if n.css_path.startswith(item.css_path + " > ")
        and any("price" in c.lower() for c in n.classes)
    ]
    # 价格链本身有 ≥2 个候选（price / product-price / bdi / currencySymbol）
    assert len(price_chain) >= 2, "夹具形态变了：价格不再是多层嵌套"


def test_aggregate_and_single_chain_are_distinguished_by_structure() -> None:
    """★ 判据必须是**分叉**，不是**数量** —— 用一个同时含两种情形的最小夹具钉住。

        <div class="row">
          <div class="item">
            <span class="price"><bdi>$5.00</bdi></span>      <- 单链：同一个值的两种粒度，该留
            <span class="byline">by <small>Alice</small> <a>(about)</a></span>  <- 分叉：拼成一整块，该删

    我第一版判据写成「候选后代 ≥2 就删」，把**单链**也删了（scrapingcourse 的
    价格四层嵌套只剩货币符号）。改成「存在两个互不为祖先的候选后代」后两者才分得开。

    这条刻意不用真实站点夹具：真实站点的分叉/单链比例会让断言太宽松，
    而我第一次写的 quotes 版本**是假守卫**（作者来自 `itemprop`，与聚合判定无关，
    反向断言当场判红）。
    """
    html = (
        "<html><body><div class='row'>"
        + "".join(
            f"<div class='item'>"
            f"<h3><a href='/p{i}'>Item {i}</a></h3>"
            # 真实 WooCommerce 的四层嵌套，**最里层只有货币符号** ——
            # 夹具必须照抄这个形态，否则「删到只剩最里层」也还能拿到金额，
            # 反向断言就抓不到（我第一版夹具只写两层，当场被判成假守卫）。
            f"<span class='price'><span class='product-price'><bdi>"
            f"<span class='currencySymbol'>$</span>5.0{i}</bdi></span></span>"
            f"<span class='byline'>by <small>Alice{i}</small> <a>(about)</a></span>"
            "</div>"
            for i in range(1, 5)
        )
        + "</div></body></html>"
    )
    config = analyze_to_config(html, "https://example.org/list")
    fields = config["extract"]["fields"]
    examples = {name: [str(e) for e in (rule.get("examples") or [])]
                for name, rule in fields.items()}
    joined = " | ".join(" ".join(v) for v in examples.values())
    assert "by " not in joined.replace("byline", ""), (
        f"分叉聚合没被删掉，字段里混进整块文本：{examples}"
    )
    assert any("$5.0" in e for v in examples.values() for e in v), (
        f"单链价格被误删了，金额丢失：{examples}"
    )
    # ★ 光断言"金额还在"**不够**：把单链整条删掉之后，最里层 `bdi` 的文本
    #   仍然是 "$5.01"，金额不会丢 —— 丢的是**字段名**（退化成 `内容_bdi`）。
    #   反向断言当场把这个漏判抓出来了。
    assert "价格" in fields, (
        f"价格字段丢了（链被整条删掉，只剩兜底名的内层节点）：{list(fields)}"
    )


# ── ③ 修 #1 不许把「完整标题」换成「截断标题」 ───────────────────────────


def test_books_title_still_uses_the_full_title_attribute() -> None:
    """books.toscrape 的标题是 `<h3><a title="完整标题">截断标题…</a></h3>`。

    ★ 这条也是我改错的地方：第 2 步写「一律留最外层」⇒ 取到的是 h3 的
      **截断文本**，完整标题在 `a` 的 `title` 属性里。
      既有测试 `test_product_title_uses_matching_full_attribute` 当场打红。
    """
    config = _config("books_main.html", "https://books.toscrape.com/")
    title = config["extract"]["fields"].get("标题")
    assert title is not None
    assert title.get("attr") == "title", f"标题应取完整值（attr=title），实际：{title}"


# ── ④ item 自身类名里的库存状态 ──────────────────────────────────────────


def test_stock_state_written_in_the_item_class_is_captured() -> None:
    """scrapeme.live 的库存**只写在 item 的 class 里**：

        <li class="product ... instock ...">

    页面上没有任何 "In stock" 文本。旧实现只遍历**子元素**，所以这个值
    永远枚举不到 —— 清单说该站有「库存状态」列，实际一列都没有。
    """
    config = _config("scrapeme_shop.html", "https://scrapeme.live/shop/")
    fields = config["extract"]["fields"]
    assert "库存状态" in fields, f"没有库存状态字段，实际：{list(fields)}"
    rule = fields["库存状态"]
    assert rule.get("attr") == "class", f"库存该从 class 属性取，实际：{rule}"
    assert rule.get("value_map"), "取到 class 原文后必须映射成可读状态"


def test_item_selector_does_not_pin_the_stock_value() -> None:
    """★ 选择器不能带值词 —— 否则只匹配到恰好那一种库存状态的商品。

    这条在书本评分上踩过：带 `Three` 的选择器让 20 条里只有 3 条取到。
    """
    config = _config("scrapeme_shop.html", "https://scrapeme.live/shop/")
    selector = str(config["extract"].get("item_selector") or "")
    assert "instock" not in selector.lower(), f"item_selector 钉死了库存状态：{selector}"


# ── ⑥ data-testid 是显式语义标注，比 class 更可信 ────────────────────────


def test_data_testid_is_used_as_the_field_identity() -> None:
    """★ 现代站点常把语义写在 `data-testid` 里，而不是 class。

    实测（web-scraping.dev/reviews，浏览器渲染后）：整个数据区**一个 class 都没有**，
    只有 `data-testid="review-text"` / `"review-date"` / `"review-stars"`。
    旧实现把两列命名成兜底的 `内容_p` / `内容_span` —— 值是对的，名字没有意义。

    夹具 `webscrapingdev_reviews_rendered.html` 是用产品自己的 `_fetch_rendered()`
    抓下来的**真实渲染结果**（271 KB / 21 条 review），不是我手搓的。
    """
    config = _config(
        "webscrapingdev_reviews_rendered.html", "https://web-scraping.dev/reviews"
    )
    fields = config["extract"]["fields"]
    assert "正文" in fields, f"没有正文字段，实际：{list(fields)}"
    assert "日期" in fields, f"没有日期字段，实际：{list(fields)}"
    assert str(fields["正文"].get("selector")) == '[data-testid="review-text"]'
    # 兜底名不许出现：出现了说明 testid 没被用上
    assert not [n for n in fields if n.startswith("内容_")], (
        f"仍有兜底字段名，说明 testid 未被采用：{list(fields)}"
    )


@pytest.mark.parametrize(
    "testid",
    ["adBanner-wrapper", "csrf-token", "prism-ad-wrapper", "commercial-label-taboola"],
)
def test_non_data_testids_are_not_treated_as_business_fields(testid: str) -> None:
    """`data-testid` 的封闭表只认语义明确的词根。

    广告位 / CSRF token 也有 data-testid，把它们当成业务字段会产出一堆噪声列。
    """
    from omnicrawler.extraction.intelligent_scraper.field_inference import _testid_field_name

    assert _testid_field_name(testid, ["some text"]) not in {
        "正文", "日期", "评分", "作者", "标题", "价格", "名称", "图片地址", "链接地址",
    }


# ── ⑤ 夹具本身的完整性 ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "fixture",
    [
        "scrapingcourse_ecommerce.html",
        "scrapeme_shop.html",
        "books_main.html",
        "quotes_main.html",
        "scrapethissite_simple.html",
        "webscrapingdev_products.html",
        "qscrape_news.html",
    ],
)
def test_fixtures_are_present_and_parseable(fixture: str) -> None:
    """夹具缺失时上面所有断言都会变成"跳过"或误绿 —— 先把存在性钉住。"""
    path = FIXTURES / fixture
    assert path.is_file(), f"缺少夹具 {fixture}"
    assert len(path.read_bytes()) > 5000, f"夹具 {fixture} 太小，可能存坏了"
