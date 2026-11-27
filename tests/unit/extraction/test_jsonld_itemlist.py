"""JSON-LD `ItemList` 也是「内嵌数据」（2026-11-27）。

## 为什么值得单独钉

很多站点的**唯一**结构化列表在 `<script type="application/ld+json">` 的 `ItemList` 里：
DOM 只有一个大标题，正文是脚手架。实测这类页面走结构分析得到 `page_type=single`，
产出一份「只有一个 `标题_1`（取值是页面大标题）」的配置 ——
**`analysis.fields` 非空，于是旧判据直接放过，用户拿到一份没有业务记录的配置**。

所以判据从「DOM 完全没字段」放宽到「**DOM 里没有列表**」，同时加两条闸：
只认真列表（>= 3 条）、详情页不换链路。

## 与 jsonld 来源的边界

`jsonld` 来源 = 整页那**一份**描述，取一次、单独成 document 记录。
`ItemList` = **列表**，每条 entry 一条记录。两者都读 LD，但粒度相反。
这条边界不写清楚，后来者很容易把它们合并，于是列表又变回一个单元格。
"""

from __future__ import annotations

from omnicrawler.extraction.embedded_json import extract_itemlist_payloads
from omnicrawler.extraction.intelligent_scraper import analyze_to_config, verify_config

ITEM_LIST_HTML = (
    "<!DOCTYPE html><html><head>"
    '<script type="application/ld+json">'
    '{"@context":"https://schema.org","@type":"ItemList","itemListElement":['
    '{"@type":"ListItem","position":1,"url":"https://ex.org/a","name":"Alpha","description":"first"},'
    '{"@type":"ListItem","position":2,"url":"https://ex.org/b","name":"Beta","description":"second"},'
    '{"@type":"ListItem","position":3,"url":"https://ex.org/c","name":"Gamma","description":"third"}'
    ']}'
    "</script></head><body><div class='hero'><h1>Articles</h1></div></body></html>"
)


def test_item_list_entries_become_a_record_set() -> None:
    payloads = extract_itemlist_payloads(ITEM_LIST_HTML)
    assert len(payloads) == 1
    assert payloads[0].source == "jsonld#ItemList"
    assert len(payloads[0].payload["itemListElement"]) == 3


def test_single_item_list_is_not_a_record_set() -> None:
    """1 条 entry 的 ItemList 是"某条记录里的一个列表"，不是列表页。"""
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@type":"ItemList","itemListElement":[{"@type":"ListItem","name":"only"}]}'
        "</script></head><body></body></html>"
    )
    assert extract_itemlist_payloads(html) == []


def test_breadcrumb_list_is_not_treated_as_records() -> None:
    """BreadcrumbList 是导航，不是业务列表 —— 认成记录会交付一串假行。"""
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@type":"BreadcrumbList","itemListElement":['
        '{"@type":"ListItem","position":1,"name":"Home"},'
        '{"@type":"ListItem","position":2,"name":"Shop"}]}'
        "</script></head><body></body></html>"
    )
    assert extract_itemlist_payloads(html) == []


def test_graph_wrapper_is_unwrapped() -> None:
    """`@graph` 是 schema.org 的容器写法，ItemList 常包在里面。"""
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@context":"https://schema.org","@graph":['
        '{"@type":"WebSite","name":"X"},'
        '{"@type":"ItemList","itemListElement":['
        '{"@type":"ListItem","name":"A"},{"@type":"ListItem","name":"B"}]}'
        "]}</script></head><body></body></html>"
    )
    payloads = extract_itemlist_payloads(html)
    assert len(payloads) == 1
    assert len(payloads[0].payload["itemListElement"]) == 2


def test_analyze_prefers_item_list_over_a_lonely_dom_heading() -> None:
    """★ 这条是整件事的动机：以前这份页面会产出"只有一个 标题_1"的配置。"""
    config = analyze_to_config(ITEM_LIST_HTML, "https://ex.org/list")
    assert config["extract"]["embedded_json"]["item_path"] == "$.itemListElement[*]"
    assert config["extract"]["embedded_json"]["source"] == "jsonld#ItemList"
    fields = config["extract"]["fields"]
    assert "名称" in fields and "描述" in fields
    # ★ 结构元数据不是业务字段：@type / position 各占一列且全是同一个值
    assert "@type" not in fields, fields
    assert "position" not in fields, fields


def test_item_list_config_verifies_with_the_real_record_count() -> None:
    config = analyze_to_config(ITEM_LIST_HTML, "https://ex.org/list")
    report = verify_config(config, ITEM_LIST_HTML)
    assert report["mode"] == "embedded"
    assert report["items"] == 3
    assert report["records"] == 3


def test_missing_jsonld_still_falls_back_to_dom_list() -> None:
    """反方向：普通 DOM 列表页**不许**被这条新判据改走 JSON 链路。"""
    rows = "".join(
        f"<div class='item'><h3 class='title'>T{i}</h3>"
        f"<span class='price'>${i}.00</span></div>"
        for i in range(1, 7)
    )
    html = f"<html><body><div class='list'>{rows}</div></body></html>"
    config = analyze_to_config(html, "https://ex.org/list")
    assert "embedded_json" not in config["extract"]
    assert config["extract"].get("item_selector"), config["extract"]
