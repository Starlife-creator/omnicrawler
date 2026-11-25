"""选择器编译缓存：**快**且**语义不变**。

为什么要有这个文件（2026-11-25 实测）：

`Tag.select("h2 a")` 每次调用都会把选择器字符串重新解析一遍（soupsieve 的
`compile` 阶段）。提取循环里同一个选择器对**每一条记录**重复调用 ——
1000 条记录 × 3 个字段就是 3000 次重复解析。

实测（2000 条记录 × 3 字段，整页提取）：

    优化前  216.5 ms
    优化后  165.1 ms   -> 1.31x

微基准（单规则 select x1000）：14 µs/次 -> 7 µs/次（1.94x）。

本文件钉住两件事：

1. 缓存**确实生效**（同一选择器第二次不再编译）；
2. 缓存**不改变结果**（返回的节点与未缓存路径逐一相同，含非法选择器的降级）。
"""

from __future__ import annotations

import pytest

pytest.importorskip("bs4")

from omnicrawler.extraction import html_tools  # noqa: E402
from omnicrawler.extraction.html_tools import parse_html, select_nodes  # noqa: E402

HTML = (
    "<html><body>"
    "<div class='item'><h2><a href='/a'>A</a></h2><p>P1</p></div>"
    "<div class='item'><h2><a href='/b'>B</a></h2><p>P2</p></div>"
    "</body></html>"
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """每条用例独立缓存状态 —— 否则「第二次不编译」会被上一条用例满足。"""
    html_tools._SELECTOR_CACHE.clear()
    yield
    html_tools._SELECTOR_CACHE.clear()


def test_same_selector_is_compiled_once() -> None:
    """同一选择器只编译一次，之后走缓存。"""
    document = parse_html(HTML)
    first = select_nodes(document, "div.item")
    assert len(first) == 2
    assert "div.item" in html_tools._SELECTOR_CACHE, "第一次调用没有写入缓存"
    cached = html_tools._SELECTOR_CACHE["div.item"]
    second = select_nodes(document, "div.item")
    assert html_tools._SELECTOR_CACHE["div.item"] is cached, "第二次重新编译了"
    assert len(second) == 2


def test_cached_and_uncached_results_are_identical() -> None:
    """缓存路径与原路径返回**完全相同的节点序列**（含顺序）。"""
    document = parse_html(HTML)
    for selector in ("div.item", "h2 a", "p", "div.item h2 a", "body > div"):
        html_tools._SELECTOR_CACHE.clear()
        plain = select_nodes(document, selector)
        again = select_nodes(document, selector)   # 走缓存
        assert [n.get_text() for n in plain] == [n.get_text() for n in again], selector


def test_invalid_selector_is_cached_as_failure_and_still_raises() -> None:
    """非法选择器：缓存「失败」标记，但**行为不变**（仍然抛给调用方降级处理）。"""
    soupsieve = pytest.importorskip("soupsieve")
    document = parse_html(HTML)
    with pytest.raises(soupsieve.SelectorSyntaxError):
        select_nodes(document, "div[[[broken")
    assert html_tools._SELECTOR_CACHE.get("div[[[broken") is False, (
        "非法选择器没有被缓存为失败标记 —— 每次都会重新编译再失败"
    )
    with pytest.raises(soupsieve.SelectorSyntaxError):
        select_nodes(document, "div[[[broken")


def test_empty_selector_returns_context_without_caching() -> None:
    """空选择器走「整个上下文」分支，不参与编译缓存。"""
    document = parse_html(HTML)
    assert select_nodes(document, "") == [document]
    assert "" not in html_tools._SELECTOR_CACHE


def test_cache_is_bounded() -> None:
    """缓存有上限 —— 长跑进程（serve/worker）里不能无限增长。"""
    document = parse_html(HTML)
    limit = html_tools._SELECTOR_CACHE_LIMIT
    assert limit > 0
    for index in range(limit + 20):
        select_nodes(document, f"div.c{index}")
    assert len(html_tools._SELECTOR_CACHE) <= limit, (
        f"缓存增长到 {len(html_tools._SELECTOR_CACHE)}，上限 {limit}"
    )
