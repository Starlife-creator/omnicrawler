from omnicrawler.extraction.intelligent_scraper import _classify_field


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


# ── 走查 R3.4：真实站点的形态必须被覆盖 ────────────────────────────────
#
# 背景（0.13.0 实测）：上面的断言只覆盖 `span.price` / `div.product-price`，
# **恰好都是白名单内能通过的形态**；而实测目标站点用的是
# `<div class="product_price"><p class="price_color">` ——
# `p` 不在价格规则的标签白名单里，整列被命名成兜底名 `内容_p`。
# 「判据写了，但样本选得绕开了缺口」正是这次走查要修的一类问题。


def test_real_world_p_tag_price_is_recognized() -> None:
    assert _classify_field("p", "price_color", ["£51.77"]) == "价格"


def test_ancestor_class_is_used_only_as_fallback() -> None:
    # 自身没有语义信号时，祖先类名兜底
    assert _classify_field("span", "value", ["9.9"], "product_price") == "价格"
    # ★ 自身有语义信号时**必须优先**：否则同一个 product_price 下的两个子元素
    #   会一起被命名成「价格」（实测把"库存"列命名成了价格，还因排序压过真价格列）
    assert _classify_field("p", "instock availability", ["In stock"], "product_price") == "库存状态"
    assert _classify_field("p", "price_color", ["£51.77"], "product_price") == "价格"


def test_ancestor_fallback_still_respects_tag_gate() -> None:
    """祖先兜底不得越过标签门禁 —— 否则任意标签都会被乱命名。"""
    # `img` 不在价格规则的标签白名单里，即使祖先叫 product_price 也不该命名成价格
    assert _classify_field("img", "shot", [], "product_price") == "图片地址"


def test_constant_business_value_keeps_its_semantics() -> None:
    """走查 R3.5：恒定文案过滤必须放行"取值恒定但有语义类名"的真实业务列。

    实测反例：`<p class="instock availability">In stock</p>` 在样本内取值恒定，
    旧判据把它整列删掉 ⇒ 「库存状态」从未进入候选（不是命名错，是没枚举到）。
    """
    from omnicrawler.extraction.intelligent_scraper import _has_semantic_signal

    assert _has_semantic_signal("p", ["instock", "availability"]) is True
    assert _has_semantic_signal("p", ["price_color"]) is True
    # 无类名 / 无语义类名 ⇒ 仍按模板噪声处理（保持既有"恒定文案过滤"的意图）
    assert _has_semantic_signal("span", ["page-number"]) is False
    assert _has_semantic_signal("p", []) is False


def test_duplicate_names_get_sequential_suffixes() -> None:
    """走查 R3.4：同名消歧的后缀必须是"第几个同类列"，而不是无关的计数。

    ★ 必须**交错命名**才能体现差异：只连续取同一个名字时，旧实现
    ``len(used_names) + 1`` 恰好也会给出 2、3，看不出问题。
    """
    from omnicrawler.extraction.intelligent_scraper import _unique_field_name

    used: set[str] = set()
    seen: dict[str, int] = {}

    first = _unique_field_name("价格", used, seen)
    used.add(first)
    other = _unique_field_name("标题", used, seen)  # 中间插入另一个名字
    used.add(other)
    second = _unique_field_name("价格", used, seen)
    used.add(second)

    assert first == "价格"
    assert other == "标题"
    assert second == "价格_2", "后缀应是第几个同类列，而不是无关的计数"
