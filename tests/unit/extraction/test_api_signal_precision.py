"""`rest-json` API 信号：路径形态才算，字面量 `application/json` 不算。

## 事故形态（2026-11-27 实测）

一个**讲解 HTTP 头**的文档页，正文里写着
「Send Content-Type: application/json to the server」。旧实现把
`application/json` 当成 API 信号 ⇒ `api_signals = ("rest-json",)`，
**推荐第一位变成 `generic/spa-api-discovery`**（「开浏览器去抓后台 API」）——
而那个页面根本没有 API。

## 判据

| 信号 | 是否构成 API 页 |
|---|---|
| 路径形态 `/api/`、`/wp-json/` | ✅ 是 |
| 真实调用 `fetch(`、`xhr`、`graphql` | ✅ 是 |
| 字面量 `application/json` **单独**出现 | ❌ 不是（文档 / 代码示例 / content-type 说明都会出现）|
| `application/json` **配合**任一真实调用 | ✅ 是（补充确认）|

★ 关键是最后一行：收紧判据不能把「真的用 JSON API」的页面也判掉。
"""

from __future__ import annotations

from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.sources.site_inspector import inspect_result
from omnicrawler.templates.template_catalog import bundled_template_catalog


def _api_signals(body: bytes) -> tuple[str, ...]:
    request = CrawlRequest("https://example.org/")
    result = FetchResult(
        request, request.url, 200, {"content-type": "text/html"}, body, 0.01
    )
    return inspect_result(result, bundled_template_catalog()).api_signals


def test_docs_mentioning_application_json_is_not_an_api_page() -> None:
    """★ 这条就是事故：文档正文提到 content-type 不等于页面在调 API。"""
    body = b"<html><body><p>Send Content-Type: application/json to the server.</p></body></html>"
    assert "rest-json" not in _api_signals(body)


def test_api_path_alone_is_enough() -> None:
    body = b'<html><body><script>var u = "/api/v2/items";</script></body></html>'
    assert "rest-json" in _api_signals(body)


def test_wp_json_path_is_enough() -> None:
    body = b'<html><body><script>var u = "/wp-json/wp/v2/posts";</script></body></html>'
    assert "rest-json" in _api_signals(body)


def test_real_call_plus_json_literal_counts() -> None:
    """★ 反方向：真有调用时，`application/json` 作为补充确认要能加上。

    这条守住「收紧判据没有把真的 API 页判掉」。
    """
    body = (
        b"<html><body><p>application/json</p>"
        b"<script>fetch(\"/x\")</script></body></html>"
    )
    assert "rest-json" in _api_signals(body)


def test_json_literal_without_any_call_is_not_enough() -> None:
    body = b"<html><body><p>application/json</p></body></html>"
    assert "rest-json" not in _api_signals(body)


def test_docs_page_does_not_recommend_api_discovery_first() -> None:
    """★ 端到端：文档页的第一推荐不该是 `generic/spa-api-discovery`。"""
    request = CrawlRequest("https://example.org/docs")
    body = (
        b"<html><body><h1>HTTP headers</h1>"
        b"<p>Send Content-Type: application/json to the server.</p>"
        b"<script>function load(){}</script></body></html>"
    )
    result = FetchResult(
        request, request.url, 200, {"content-type": "text/html"}, body, 0.01
    )
    report = inspect_result(result, bundled_template_catalog())
    top = report.recommendations[0]["id"] if report.recommendations else ""
    assert top != "generic/spa-api-discovery", (
        f"文档页不该首推 API 发现：{[r['id'] for r in report.recommendations]}"
    )
