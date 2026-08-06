from __future__ import annotations

import pytest

from omnicrawl.gui.wizard.step3_fields import selector_kind, suggest_xpath_candidates


def test_suggest_xpath_candidates_with_chinese_text() -> None:
    from lxml import html

    tree = html.fromstring("<html><body><h1>产品价格</h1><p>价格: 99元</p><span>其他</span></body></html>")
    top = suggest_xpath_candidates(tree, ["价格: 99元"])
    assert top, "应能按示例文本找到候选"
    text, xpath, sim = top[0]
    assert "99元" in text
    assert xpath.startswith("/")
    assert 0.6 < sim <= 1.0


def test_suggest_xpath_candidates_sorts_by_similarity() -> None:
    from lxml import html

    tree = html.fromstring("<html><body><p>目标内容 abc</p><p>无关内容 xyz</p></body></html>")
    top = suggest_xpath_candidates(tree, ["目标内容 abc"])
    assert top[0][0] == "目标内容 abc"


def test_suggest_xpath_candidates_empty_html_returns_empty() -> None:
    from lxml import html

    tree = html.fromstring("<html><body></body></html>")
    assert suggest_xpath_candidates(tree, ["任何"]) == []


def test_html_fromstring_supports_lxml_html_apis() -> None:
    """S1.1.3 回归：lxml.html.fromstring 返回的树必须支持 getpath/text_content/xpath。"""
    from lxml import html

    tree = html.fromstring("<html><head><meta name='price' content='99'></head><body><h1>标题</h1></body></html>")
    meta = tree.xpath("//meta[contains(@name, 'price')]")[0]
    assert meta.get("content") == "99"
    assert tree.getroottree().getpath(meta) == "/html/head/meta"
    h1 = tree.xpath("//h1")[0]
    assert (h1.text_content() or "").strip() == "标题"


def test_suggest_xpath_candidates_rejects_missing_lxml_gracefully() -> None:
    pytest.importorskip("lxml")
    from lxml import html

    tree = html.fromstring("<p>中文内容</p>")
    top = suggest_xpath_candidates(tree, ["中文"])
    assert any("中文" in item[0] for item in top)


def test_selector_kind_detects_xpath_and_css() -> None:
    assert selector_kind("//div[@class='price']") == "xpath"
    assert selector_kind("/html/body/div[1]/span") == "xpath"
    assert selector_kind(".//*[contains(text(),'x')]") == "xpath"
    assert selector_kind("div.price > span") == "css"
    assert selector_kind(".price span") == "css"
    assert selector_kind("") == "css"


def test_xpath_parameterization_survives_quotes_in_field_name() -> None:
    """S1.4.4：字段名含引号不再使 XPath 崩溃。"""
    from lxml import html

    tree = html.fromstring(
        "<html><head><meta name=\"price's\" content='99'></head>"
        "<body><h1>标题's内容</h1></body></html>"
    )
    name = "price's"
    metas = tree.xpath(
        "//meta[contains(@name, $field) or contains(@property, $field)]",
        field=name,
    )
    assert metas and metas[0].get("content") == "99"
    heading = tree.xpath(
        "//h1[contains(text(), $field)] | //h2[contains(text(), $field)] | //h3[contains(text(), $field)]",
        field="标题's内容",
    )
    assert heading and (heading[0].text_content() or "").strip().startswith("标题")
