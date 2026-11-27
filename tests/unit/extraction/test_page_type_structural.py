"""`page_type` 必须**优先复用抽取器的结构化判断**，而不是自己数标签。

## 事故形态（2026-11-27 实测）

`scrapethissite.com/pages/simple/` 有 **250 张国家卡片**，但只有 **7 个 `<a>`**
（卡片本身不是链接）。

| 模块 | 判据 | 结论 |
|---|---|---|
| `site_inspector`（本模块，旧）| `links >= 20` | **unknown** ⇒ 推荐 `generic/single-page` |
| `intelligent_scraper.analyze_page` | 250 个重复结构 | **list** |

**同一个产品里两处判据打架**，用户拿到的是差的那个。

## 判据

优先用抽取器的结构化判断（它看的是重复结构的真实形态）；
它给不出结论（`single` / `unknown`）时才回落到计数启发式。

失败一律回落 —— 辅助判据不引入新的失败面。
"""

from __future__ import annotations

from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.sources.site_inspector import inspect_result
from omnicrawler.templates.template_catalog import bundled_template_catalog


def _inspect(body: str):
    request = CrawlRequest("https://example.org/x")
    result = FetchResult(
        request, request.url, 200, {"content-type": "text/html"}, body.encode(), 0.01
    )
    return inspect_result(result, bundled_template_catalog())


def _cards(count: int, *, with_links: bool) -> str:
    cells = []
    for i in range(count):
        inner = f'<a href="/p/{i}">Item {i}</a>' if with_links else f"<h3>Item {i}</h3>"
        cells.append(f'<div class="card">{inner}<span class="v">{i}</span></div>')
    return f'<div class="grid">{"".join(cells)}</div>'


def test_card_list_without_links_is_still_a_list() -> None:
    """★ 这条就是事故：250 张卡片、0 个链接，仍是列表页。"""
    body = f"<html><body>{_cards(30, with_links=False)}</body></html>"
    assert _inspect(body).page_type == "list"


def test_card_list_with_links_is_a_list() -> None:
    """有链接的卡片列表当然也是列表（旧判据能过，这里防回退）。"""
    body = f"<html><body>{_cards(30, with_links=True)}</body></html>"
    assert _inspect(body).page_type == "list"


def test_card_list_recommends_list_detail_not_single_page() -> None:
    """★ 端到端：卡片列表的首推不该是 `generic/single-page`。"""
    body = f"<html><body>{_cards(30, with_links=False)}</body></html>"
    report = _inspect(body)
    assert report.recommendations[0]["id"] != "generic/single-page", (
        [r["id"] for r in report.recommendations]
    )


def test_detail_page_is_still_detail() -> None:
    """反方向：详情页判据不能被这条改动带偏。"""
    body = "<html><body><article><h1>Title</h1><p>Body text here.</p></article></body></html>"
    assert _inspect(body).page_type == "detail"


def test_bare_page_still_unknown() -> None:
    """极简页面既不是列表也不是详情，保持 unknown。"""
    assert _inspect("<html><body><p>hi</p></body></html>").page_type == "unknown"
