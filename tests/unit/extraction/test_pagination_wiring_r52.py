"""分页信号接入自动分析、iframe 产出定位信息（走查 R5.2）。

## 背景（0.13.0 端到端走查）

- `web-scraping.dev/pagination`（c42）与 iframe 页（c16）的 `auto-analyze` **退出码 3**：
  「自动分析未能产出可用配置」。
- ★ 复核代码后发现分页**不是"没写"**，而是**写得不完整**：旧实现写的是
  ``{type: page, parameter: X}`` —— **没有 `end`**。而 `sources.py` 的默认是
  ``end = start`` ⇒ 运行期只发第 1 页；`validate_pagination` 只对**游标形状**要求
  `next_path`，对页码形状不要求 `end` ⇒ **校验通过、只抓一页**。
  这正是本批要修的东西：**"配了等于没配"**。

## 判据（三件事都要钉住）

1. **形状必须落在契约里**：`detect_pagination` 产出 `url_param` / `next_link`，
   而 `core/pagination.py` 只认 `page` / `cursor` —— 原样写入会被 `detect_shape`
   判成 `None`（校验通过、不翻页）。所以必须**显式翻译**，且翻译结果要被
   `detect_shape` 认作 `PAGE_SHAPE`。
2. **`end` 必须有值**：缺 `end` = 只抓第一页。取不到页码范围时**宁可不写配置**、
   只给建议（不猜一个数）。
3. **不自动生成点击**：「下一页」是同站 `<a>`，由 `crawl` 的通用链接发现跟进；
   历史实现把它写成 `browser.actions` 的"点击下一页"，导致入口页内容全丢
   （quotes.toscrape.com/js 实测 0 条）—— 只给提醒，且提醒要说明**它已经被怎么处理**。
"""

from __future__ import annotations

import pytest

from omnicrawler.core.pagination import PAGE_SHAPE, detect_shape, validate_pagination
from omnicrawler.extraction.intelligent_scraper import (
    _iframe_locators,
    _interaction_advice,
    _interaction_failure_hint,
    _max_page_number,
    _page_parameter_in,
    _pagination_config,
    analyze_to_config,
)

URL = "https://example.org/products"

_ROWS = "".join(
    f'<li class="pod"><h3><a href="/p/{i}">商品 {i}</a></h3>'
    f'<p class="price_color">¥{i}.00</p></li>'
    for i in range(1, 7)
)

PAGED = (
    "<html><body><ol class='row'>" + _ROWS + "</ol>"
    "<nav class='pagination'>"
    "<a href='?page=1'>1</a><a href='?page=2'>2</a><a href='?page=3'>3</a>"
    "<a href='?page=4'>4</a><a rel='last' href='?page=5'>末页</a>"
    "</nav></body></html>"
)

NEXT_LINK = (
    "<html><body><ol class='row'>" + _ROWS + "</ol>"
    "<a id='next' rel='next' href='/products/page/2'>下一页</a>"
    "</body></html>"
)

LOAD_MORE = (
    "<html><body><ol class='row'>" + _ROWS + "</ol>"
    "<button class='load-more' id='more'>加载更多</button>"
    "<script>new IntersectionObserver(function(){},{});</script>"
    "</body></html>"
)

IFRAMED = (
    "<html><body><h1>国家数据</h1>"
    "<iframe id='data-frame' name='data' src='https://frames.example.org/countries'></iframe>"
    "</body></html>"
)


# ── 1. 页码取值 ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("html", "parameter", "expected"),
    [
        ("<a href='?page=2'>2</a><a href='?page=9'>9</a>", "page", 9),
        ("<a href='/x?page=3'>3</a><a rel='last' href='/x?page=17'>末</a>", "page", 17),
        ("<a href='?page=0'>0</a>", "page", None),          # 0 不是页码
        ("<a href='?p=4'>4</a>", "page", None),             # 参数名要对得上
        ("<p>没有分页</p>", "page", None),
    ],
)
def test_max_page_number(html: str, parameter: str, expected: int | None) -> None:
    assert _max_page_number(html, parameter) == expected


@pytest.mark.parametrize(
    ("href", "expected"),
    [
        ("/x?page=2", "page"),
        ("/x?a=1&p=3", "p"),
        ("/x?offset=50", "offset"),
        ("/products/page/2", ""),
        ("/next", ""),
    ],
)
def test_page_parameter_in(href: str, expected: str) -> None:
    assert _page_parameter_in(href) == expected


# ── 2. 翻译成契约形状 ─────────────────────────────────────────────────


def test_shape_must_be_the_contract_shape() -> None:
    """★ 本条钉住根因：`url_param` 不是契约形状，原样写入等于没写。"""
    assert detect_shape({"type": "url_param", "param": "page"}) is None, (
        "契约里没有 url_param —— 所以检测结果**必须翻译**，不能原样写进配置"
    )
    assert detect_shape({"type": "page", "parameter": "page"}) is PAGE_SHAPE


def test_paged_shape_is_complete_and_valid() -> None:
    """★ 回归守卫：**`end` 必须有值** —— 旧实现只写 `type` + `parameter`，
    而 `sources.py` 的默认是 `end = start` ⇒ 只抓第 1 页、校验还是通过。"""
    shape, notes = _pagination_config(PAGED, {"type": "url_param", "param": "page"})
    assert shape == {"type": "page", "parameter": "page", "start": 1, "end": 5, "step": 1}
    assert detect_shape(shape) is PAGE_SHAPE
    assert validate_pagination(shape) == []
    assert notes and "第 1–5 页" in notes[0]


def test_paged_shape_without_page_range_is_not_written() -> None:
    """页码取不到 ⇒ **不写配置**（写了也只能抓第一页，是假配置），只给可执行建议。"""
    html = "<html><body><a href='?page=1'>1</a></body></html>"
    shape, notes = _pagination_config(html, {"type": "url_param", "param": "page"})
    assert shape is None
    assert notes and "end: N" in notes[0]


def test_next_link_pages_with_a_page_parameter_become_page_shape() -> None:
    detected = {"type": "next_link", "xpath": "//a", "example_href": "/products?page=2"}
    shape, _notes = _pagination_config(PAGED, detected)
    assert shape is not None and shape["parameter"] == "page"


def test_next_link_without_a_parameter_is_reported_not_clicked() -> None:
    """★ 不生成点击动作（历史教训），但要说明**它已经被怎么处理**（链接发现）。"""
    shape, notes = _pagination_config(NEXT_LINK, {
        "type": "next_link", "xpath": "//a[rel='next']", "example_href": "/products/page/2",
    })
    assert shape is None
    text = "".join(notes)
    # ★ 断言要落在「说明它**被跟进**」这个意思上 —— 只说"链接发现"太松：
    #   提醒里还有一句「若链接发现翻不到…」，删掉前半句仍然能命中。
    assert "通用链接发现" in text, "只说不做什么、不说已经怎么处理了 —— 用户会以为整页没人管"
    assert "//a[rel='next']" in text, "没给出该链接的定位信息"


def test_unknown_or_absent_detection_yields_nothing() -> None:
    assert _pagination_config(PAGED, None) == (None, [])
    assert _pagination_config(PAGED, {"type": "whatever"}) == (None, [])


# ── 3. 端到端：四类形态各一条 ─────────────────────────────────────────


def test_paged_page_writes_pagination_into_the_config() -> None:
    advisories: list[str] = []
    config = analyze_to_config(PAGED, URL, advisories=advisories)
    pagination = config["source"]["pagination"]
    assert pagination == {"type": "page", "parameter": "page", "start": 1, "end": 5, "step": 1}
    assert validate_pagination(pagination) == []
    assert any("已写入" in line for line in advisories)


def test_next_link_page_write_no_pagination_and_says_so() -> None:
    advisories: list[str] = []
    config = analyze_to_config(NEXT_LINK, URL, advisories=advisories)
    assert "pagination" not in config["source"]
    assert any("链接发现" in line for line in advisories)


def test_scroll_shape_produces_browser_actions_not_pagination() -> None:
    """加载更多 / 无限滚动走 R3.2 的浏览器滚动动作，与分页配置**互不冲突**。"""
    config = analyze_to_config(LOAD_MORE, URL, scroll_rounds=12)
    assert "pagination" not in config["source"]
    actions = config["browser"]["actions"]
    assert [item["action"] for item in actions] == ["wait_ms", "scroll_bottom"]
    assert config["source"]["kind"] == "browser"


def test_iframe_locators_are_reported() -> None:
    """iframe 要产出**定位信息**（R5.2 的原文要求）—— 只说"内容在 iframe 里"没有用。"""
    assert _iframe_locators(IFRAMED) == (
        "iframe#data-frame（src=https://frames.example.org/countries）",
    )
    advice = "".join(_interaction_advice(("iframe",), scroll_rounds=0, iframe_locators=_iframe_locators(IFRAMED)))
    assert "iframe#data-frame" in advice
    hint = "".join(_interaction_failure_hint(("iframe",), html=IFRAMED))
    assert "iframe#data-frame" in hint


def test_failure_explanation_carries_the_iframe_locator() -> None:
    """★ 失败路径也要给定位信息 —— 而且调用点必须把 html 传下去（漏传就静默没有）。"""
    from omnicrawler.extraction.intelligent_scraper import _explain_no_config, _interaction_signals

    lines = _explain_no_config(
        IFRAMED, static_top=None, rendered="", signals=_interaction_signals(IFRAMED)
    )
    assert any("iframe#data-frame" in line for line in lines), lines


@pytest.mark.parametrize(
    ("html", "expected"),
    [
        ("<html><body><iframe id='a'></iframe></body></html>", "iframe#a"),
        ("<html><body><iframe name='b'></iframe></body></html>", 'iframe[name="b"]'),
        ("<html><body><iframe src='https://x/y'></iframe></body></html>", "iframe[src="),
        ("<html><body><iframe></iframe></body></html>", "iframe:nth-of-type(1)"),
        ("<html><body><p>没有 frame</p></body></html>", ""),
    ],
)
def test_iframe_locator_priority(html: str, expected: str) -> None:
    locators = _iframe_locators(html)
    if expected:
        assert locators and expected in locators[0]
    else:
        assert locators == ()
