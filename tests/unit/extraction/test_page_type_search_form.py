"""`page_type` 判定：**有搜索框 ≠ 这是搜索结果页**。

## 事故形态（2026-11-27 实测）

一个 20 张卡片的商品列表页，顶部有最常见的导航搜索框：

    <form action="/search"><input name="q"></form>

旧判据 `if search_form: page_type = "search"` 把整页判成 `search` ⇒
推荐 `generic/single-page`（**单页**模板：不翻页、不进详情）。
去掉那个搜索框，同一页判成 `list` ⇒ 推荐 `generic/list-detail`。

★ 一个导航控件把整条采集策略带偏了 —— 而且用户看不出因果。

## 判据

搜索框只在**页面主体不是列表**时才决定类型。列表信号（重复文章块或足够多的链接）
优先 —— 那才回答"这页是干什么的"。
"""

from __future__ import annotations

from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.sources.site_inspector import inspect_result
from omnicrawler.templates.template_catalog import bundled_template_catalog


def _page_type(body: str) -> str:
    request = CrawlRequest("https://example.org/x")
    result = FetchResult(
        request, request.url, 200, {"content-type": "text/html"}, body.encode(), 0.01
    )
    return inspect_result(result, bundled_template_catalog()).page_type


_NAV_SEARCH = '<form action="/search"><input name="q"></form>'


def _cards(count: int) -> str:
    return "".join(
        f'<div class="card"><a href="/p/{i}"><h3>Item {i}</h3></a></div>'
        for i in range(count)
    )


def test_list_page_with_a_nav_search_box_is_still_a_list() -> None:
    """★ 这条就是事故：导航搜索框不该把商品列表判成搜索结果页。"""
    body = f'<html><body>{_NAV_SEARCH}<div class="grid">{_cards(20)}</div></body></html>'
    assert _page_type(body) == "list"


def test_same_page_without_the_search_box_is_also_a_list() -> None:
    """对照：去掉搜索框结果必须一样 —— 否则说明判据被那个控件左右。"""
    body = f'<html><body><div class="grid">{_cards(20)}</div></body></html>'
    assert _page_type(body) == "list"


def test_genuine_search_page_is_still_search() -> None:
    """★ 反方向：真的搜索结果页（有表单、没有列表主体）必须仍判 search。"""
    body = '<html><body><form action="/search"><input name="q" value="x"></form><p>No results found.</p></body></html>'
    assert _page_type(body) == "search"


def test_article_page_is_detail() -> None:
    assert _page_type("<html><body><article>hello</article></body></html>") == "detail"


def test_bare_page_is_unknown() -> None:
    assert _page_type("<html><body><p>hi</p></body></html>") == "unknown"


def test_nav_search_box_no_longer_changes_the_recommendation() -> None:
    """★ 端到端：搜索框不该把推荐从 list-detail 换到 single-page。"""
    request = CrawlRequest("https://example.org/shop")
    with_box = f'<html><body>{_NAV_SEARCH}<div class="grid">{_cards(20)}</div></body></html>'
    without = f'<html><body><div class="grid">{_cards(20)}</div></body></html>'
    recs = {}
    for label, body in (("with", with_box), ("without", without)):
        result = FetchResult(
            request, request.url, 200, {"content-type": "text/html"}, body.encode(), 0.01
        )
        report = inspect_result(result, bundled_template_catalog())
        recs[label] = [item["id"] for item in report.recommendations[:3]]
    assert recs["with"] == recs["without"], recs
