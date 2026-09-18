"""值写在 class 名里的元素：枚举得到、也**取得到**（走查 R3.6）。

## 背景（0.13.0 端到端走查）

books.toscrape 的评分是 ``<p class="star-rating Three">`` —— **文本为空**，值藏在类名里。
实测（`.audit-tmp/w71/probe_r35_candidates.py`）证明：该元素**根本没进候选集**，
所以「评分」列从来不存在。

★ 本批的前提是"**只放行是没用的**"：`(text or is_image)` 放行之后如果不会取值，
得到的是一列**永远为空**的字段 —— 比不出现更糟。所以三件事必须同批：

1. **枚举**（`_infer_item_fields` 的过滤条件）放行"值写在 class 名里"的元素；
2. **聚合键**（`_field_key`）去掉值词 —— 否则 `star-rating Three` / `star-rating Four`
   各成一个键，出现率低于阈值，整列在下一步仍被丢掉；
3. **取值与映射**：`attribute: class` + `value_map`（`Three` → 3），
   并且**选择器也要去掉值词** —— 带值词只匹配到恰好那个评分的商品
   （实测 20 条里只取到 3 条）。

## 判据的两条底线

- **不猜**：只认一张**封闭**的数字词表；类名里有两个数字词 ⇒ 不认；
  其余类名不带字段语义（`col-md-6 four`）⇒ 不认；
  映射表只写**观察到**的词，页面出现表外取值时**原样保留**（可见）。
- **不噪声**：纯排版类名不许产出字段；`One` 不许在 CSS 段里削掉 `OneThing`。
"""

from __future__ import annotations

import pathlib

import pytest

from omnicrawler.core.config import AppConfig
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.extraction.extractors import (
    HTMLProcessor,
    _apply_rule,
    _apply_value_map,
)
from omnicrawler.extraction.html_tools import parse_html
from omnicrawler.extraction.intelligent_scraper import (
    DOMNode,
    _class_value_node,
    _class_value_token,
    _field_key,
    _observed_value_map,
    _strip_value_classes,
    analyze_to_config,
    verify_config,
)

URL = "https://books.toscrape.com/"


def _node(**kwargs: object) -> DOMNode:
    base: dict[str, object] = {"tag": "p", "classes": [], "text": ""}
    base.update(kwargs)
    return DOMNode(**base)  # type: ignore[arg-type]


# ── 1. 「值写在 class 名里」的判定 ──────────────────────────────────────


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"classes": ["star-rating", "Three"]}, "Three"),
        ({"classes": ["star-rating", "three"]}, "three"),
        ({"classes": ["rating", "五"]}, "五"),
        ({"classes": ["star-rating", "Three"], "text": "3 星"}, None),      # 有文本 ⇒ 有别的取值来源
        ({"classes": ["star-rating", "Three"], "is_image": True}, None),
        ({"classes": ["star-rating", "Three"], "is_link": True}, None),
        ({"classes": ["star-rating", "Three"], "itemprop": "ratingValue"}, None),
        ({"classes": ["star-rating", "Three", "Four"]}, None),              # 两个数字词 ⇒ 歧义不猜
        ({"classes": ["btn", "btn-primary"]}, None),                        # 排版类名不是值
        ({"classes": []}, None),
    ],
)
def test_class_value_token(kwargs: dict, expected: str | None) -> None:
    assert _class_value_token(_node(**kwargs)) == expected


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"classes": ["star-rating", "Three"]}, True),
        ({"classes": ["rating", "五"]}, True),
        # ★ 反向：其余类名不带字段语义 ⇒ 不枚举（否则纯排版类名会产出永远的噪声列）
        ({"classes": ["col-md-6", "four"]}, False),
        ({"classes": ["Three"]}, False),
        ({"classes": ["star-rating", "Three", "Four"]}, False),
    ],
)
def test_class_value_node_requires_a_semantic_class(kwargs: dict, expected: bool) -> None:
    assert _class_value_node(_node(**kwargs)) is expected


# ── 2. 聚合键：值词不属于字段身份 ───────────────────────────────────────


def test_value_token_is_not_part_of_the_field_key() -> None:
    """★ 带着值词聚合 ⇒ 每件商品各成一个键、出现率必然低于阈值、整列被丢。"""
    three = _field_key(_node(classes=["star-rating", "Three"]))
    four = _field_key(_node(classes=["star-rating", "Four"]))
    assert three == four, f"值词混进了聚合键：{three} != {four}"
    assert "Three" not in three and "Four" not in three


def test_plain_node_key_is_unchanged() -> None:
    """反向：没有"值写在 class 名里"的节点，聚合键与从前一致。"""
    assert _field_key(_node(classes=["price_color"])) == "tag:p:price_color"
    assert _field_key(_node(classes=["a", "b", "c"])) == "tag:p:a.b"


# ── 3. 映射表只写"观察到"的词 ──────────────────────────────────────────


def test_observed_value_map_only_contains_seen_tokens() -> None:
    assert _observed_value_map(["Three", "One", "Three"]) == {"Three": 3, "One": 1}


def test_observed_value_map_ignores_unknown_words() -> None:
    assert _observed_value_map(["Whatever"]) == {}


# ── 4. 选择器去值词：按 CSS 段精确处理 ─────────────────────────────────


@pytest.mark.parametrize(
    ("selector", "tokens", "expected"),
    [
        ("p.star-rating.Three", ["Three"], "p.star-rating"),
        ("div.price > p.star-rating.Four", ["Four", "Five"], "div.price > p.star-rating"),
        ('p.star-rating[class~="Three"]', ["Three"], "p.star-rating"),
        ("p.star-rating.Three", [], "p.star-rating.Three"),
        # ★ 反向：`One` 不许削掉 `OneThing`（子串替换就会）
        ("p.OneThing.One", ["One"], "p.OneThing"),
        ("p.star-rating.Three", ["Four"], "p.star-rating.Three"),
    ],
)
def test_strip_value_classes(selector: str, tokens: list[str], expected: str) -> None:
    assert _strip_value_classes(selector, tokens) == expected


# ── 5. 取值映射（抽取侧） ──────────────────────────────────────────────


def test_value_map_matches_whole_value_and_tokens() -> None:
    mapping = {"Three": 3, "In stock": True}
    assert _apply_value_map("Three", mapping) == (3, "Three")
    assert _apply_value_map("star-rating Three", mapping) == (3, "star-rating Three")
    assert _apply_value_map("In stock", mapping) == (True, "In stock")


def test_value_map_is_case_insensitive() -> None:
    assert _apply_value_map("star-rating THREE", {"Three": 3}) == (3, "star-rating THREE")
    # ★ 整值分支也要大小写不敏感 —— 键里带空格时 token 分支兜不住，这条才验得到它
    assert _apply_value_map("IN STOCK", {"In stock": True}) == (True, "IN STOCK")


def test_value_map_keeps_unmapped_values_as_is() -> None:
    """★ 表外的取值**原样保留** —— 不猜、不丢，用户据此可以扩表。"""
    assert _apply_value_map("star-rating Six", {"Three": 3}) == ("star-rating Six", None)
    assert _apply_value_map("", {"Three": 3}) == ("", None)


def test_value_map_keeps_the_type_the_user_wrote() -> None:
    """用户写的是数字就用数字 —— 别替他降级成字符串。"""
    assert _apply_value_map("Three", {"Three": 3})[0] == 3
    assert isinstance(_apply_value_map("Three", {"Three": 3})[0], int)


def test_rule_applies_value_map_and_records_mapped_from() -> None:
    document = parse_html("<html><body><p class='star-rating Three' id='r'></p></body></html>")
    value, trace = _apply_rule(
        document, {"selector": "#r", "attr": "class", "value_map": {"Three": 3}}
    )
    assert value == 3
    assert trace["mapped_from"] == "star-rating Three", "被映射过的值必须可见"
    assert trace["raw_value"] == "star-rating Three"


def test_rule_without_value_map_is_untouched() -> None:
    document = parse_html("<html><body><p class='star-rating Three' id='r'></p></body></html>")
    value, trace = _apply_rule(document, {"selector": "#r", "attr": "class"})
    assert value == "star-rating Three"
    assert "mapped_from" not in trace


# ── 6. 端到端：真实形态的页面 ──────────────────────────────────────────


def _pods(ratings: list[str]) -> str:
    rows = "".join(
        f'<li class="pod"><h3><a href="/b/{index}">书 {index}</a></h3>'
        f'<p class="star-rating {rating}"></p>'
        f'<p class="price_color">£{index}.99</p></li>'
        for index, rating in enumerate(ratings, 1)
    )
    return f"<html><body><ol class='row'>{rows}</ol></body></html>"


RATINGS = ["Three", "One", "Four", "Five", "Two"]


def test_class_value_field_is_inferred_end_to_end() -> None:
    config = analyze_to_config(_pods(RATINGS), URL)
    fields = config["extract"]["fields"]
    assert "评分" in fields, f"评分列没枚举到：{sorted(fields)}"
    rule = fields["评分"]
    assert rule["attr"] == "class"
    assert rule["value_map"] == {"Three": 3, "One": 1, "Four": 4, "Five": 5, "Two": 2}
    assert rule["selector"] == "p.star-rating", "选择器把值词编进去了，只匹配到一种评分"
    assert verify_config(config, _pods(RATINGS))["fields"]["评分"] == len(RATINGS)


def _rows_for(config: dict, html: str) -> list[dict]:
    app = AppConfig(
        pathlib.Path("c.yaml"),
        pathlib.Path("."),
        {
            "project": {"name": "t"},
            "source": {"kind": "static_html", "seeds": [URL]},
            "extract": config["extract"],
        },
        pathlib.Path("work"),
    )
    result = FetchResult(
        request=CrawlRequest(url=URL),
        final_url=URL,
        status=200,
        headers={"content-type": "text/html; charset=utf-8"},
        body=html.encode("utf-8"),
        elapsed_seconds=0.0,
    )
    return [record.data for record in HTMLProcessor(app).process(result).records]


def _run(html: str) -> list[dict]:
    return _rows_for(analyze_to_config(html, URL), html)


def test_every_item_gets_its_own_rating() -> None:
    """★ 回归守卫：改前只有"恰好是描述里那个评分"的商品取到值（实测 20 条里 3 条）。"""
    rows = _run(_pods(RATINGS))
    assert [row.get("评分") for row in rows] == [3, 1, 4, 5, 2]


def test_unseen_rating_stays_visible() -> None:
    """★ 映射表只写**观察到**的词 ⇒ 运行页出现表外取值时**原样保留**（可见、可扩表）。

    不是空值、也不是猜出来的假数字。这条正是「不猜」在映射上的落地：
    分析页只见过 `Three`，所以表里只有 `Three`；运行页冒出 `Four` 时如实给出原文。
    """
    analyzed = _pods(["Three"] * 5)
    config = analyze_to_config(analyzed, URL)
    assert config["extract"]["fields"]["评分"]["value_map"] == {"Three": 3}

    rows = _rows_for(config, _pods(["Three", "Four", "Three", "Three", "Three"]))
    assert [row.get("评分") for row in rows] == [3, "star-rating Four", 3, 3, 3]


def test_all_observed_tokens_are_mapped() -> None:
    """分析页见过的词都进表（否则同一页里换个取值的商品会露原文）。"""
    config = analyze_to_config(_pods(["Three", "Four", "Two", "Five", "One"]), URL)
    assert config["extract"]["fields"]["评分"]["value_map"] == {
        "Three": 3, "Four": 4, "Two": 2, "Five": 5, "One": 1,
    }


def _pods_with_gap() -> str:
    """4 个商品，第 2 个**没有**评分 —— 验证"缺值"不会让整行消失。"""

    def pod(index: int, title: str, rating: str | None) -> str:
        star = f'<p class="star-rating {rating}"></p>' if rating else ""
        return (
            f'<li class="pod"><h3><a href="/b/{index}">{title}</a></h3>'
            f"{star}<p class=\"price_color\">£{index}.99</p></li>"
        )

    rows = (
        pod(1, "甲", "Three") + pod(2, "乙", None) + pod(3, "丙", "Two") + pod(4, "丁", "Four")
    )
    return f"<html><body><ol class='row'>{rows}</ol></body></html>"


def test_items_without_a_rating_still_yield_other_fields() -> None:
    """缺评分的那件不应该让整行消失 —— 其它字段照旧。"""
    rows = _run(_pods_with_gap())
    assert [row.get("标题") for row in rows] == ["甲", "乙", "丙", "丁"]
    assert [row.get("评分") for row in rows] == [3, None, 2, 4]


def test_leaf_mode_unit_is_a_class_value_element() -> None:
    """重复单元**自身**就是"值写在 class 名里"的元素（叶子模式）。

    ★ 这里最容易漏的是 `item_selector`：带着值词时只会匹配到恰好那一种取值的元素
      （实测报错"`item_selector` 只匹配到 1 个元素"）—— 所以 item_selector 与字段
      选择器**都要**去词。
    """
    ratings = ["Three", "Four", "Two", "Five", "One"]
    body = "".join(f'<p class="star-rating {rating}"></p>' for rating in ratings)
    html = f'<html><body><div class="wrap">{body}</div></body></html>'

    config = analyze_to_config(html, URL)
    assert config["extract"]["item_selector"] == "body > div.wrap > p.star-rating"
    assert config["extract"]["fields"]["评分"]["selector"] == ""
    assert config["extract"]["fields"]["评分"]["attr"] == "class"

    rows = _rows_for(config, html)
    assert [row.get("评分") for row in rows] == [3, 4, 2, 5, 1]


def test_plain_text_fields_are_unaffected() -> None:
    """反向：有文本的元素照旧走文本取值（本批不该改动它们的形态）。"""
    config = analyze_to_config(_pods(RATINGS), URL)
    price_rule = config["extract"]["fields"]["价格"]
    assert price_rule.get("attr") not in {"class"}
    assert "value_map" not in price_rule
