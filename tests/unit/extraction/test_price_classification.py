from omnicrawl.extraction.intelligent_scraper import _classify_field


def test_price_rule_escaped_dollar_no_longer_matches_everything() -> None:
    """S1.4.1：`$` 已转义，任意元素不再被误判为价格。"""
    assert _classify_field("div", "product-card", ["Tech item description"]) != "价格"


def test_real_price_still_detected() -> None:
    assert _classify_field("span", "price", ["$12.50"]) == "价格"
    assert _classify_field("div", "product-price", ["¥99"]) in {"价格"}
    assert _classify_field("strong", "售价", ["488"]) == "价格"


def test_title_and_description_not_shadowed_by_price() -> None:
    assert _classify_field("h2", "product-title", ["Wireless Mouse"]) == "标题"
    assert _classify_field("p", "desc", ["A long description here"]) == "描述"
    assert _classify_field("span", "date", ["2026-01-01"]) == "日期"
    assert _classify_field("a", "category", ["electronics"]) == "分类"


def test_non_price_field_with_dollar_text_keeps_its_type() -> None:
    # 文本里含 "USD$5" 不应把整个分类吸走
    assert _classify_field("p", "description", ["Cost: USD$5 total"]) == "描述"
