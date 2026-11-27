"""站点识别要报告「页面内嵌数据」这个信号（2026-11-27）。

## 为什么

`inspect` / `templates recommend` 的输出会引导用户选路径。对 Next.js / Nuxt 站点，
旧实现只报 `dynamic=True`（因为 DOM 是空挂载点、脚本很多），于是把人引向「开浏览器」
—— 而这类页面**静态抓取就能拿全数据**（数据在 `__NEXT_DATA__` 里）。

一个数量级的成本差，起因只是少了一条信号。
"""

from __future__ import annotations

from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.sources.site_inspector import inspect_result
from omnicrawler.templates.template_catalog import bundled_template_catalog

SPA = (
    '<html><head><script id="__NEXT_DATA__" type="application/json">'
    '{"props":{"pageProps":{"products":[{"id":1,"name":"A","price":1}]}}}'
    "</script></head><body><div id=\"__next\"></div></body></html>"
)


def _inspect(html: str, url: str = "https://shop.example/products"):
    result = FetchResult(
        CrawlRequest(url),
        url,
        200,
        {"content-type": "text/html; charset=utf-8"},
        html.encode("utf-8"),
        0.01,
    )
    return inspect_result(result, bundled_template_catalog())


def test_embedded_payload_is_reported_as_structured_data() -> None:
    report = _inspect(SPA)
    assert "embedded-json" in report.structured_data, report.structured_data


def test_ssr_template_is_recommended_before_browser_fallbacks() -> None:
    """★ 这条是整件事的目的：把用户引到**免渲染**那条路，而不是开浏览器。"""
    report = _inspect(SPA)
    ids = [item["id"] for item in report.recommendations]
    assert "generic/ssr-embedded-data" in ids, ids
    # ★ 必须排**第一**：同分时按 template_id 字典序，spa-api-discovery 会压过它 ——
    #   用户于是先看到"开浏览器做 API 发现"，而静态取内嵌数据才是省一个数量级的那条。
    assert ids[0] == "generic/ssr-embedded-data", ids
    # 且它必须排在"动态页"兜底之前（推荐是有序的，用户按顺序看）
    assert ids.index("generic/ssr-embedded-data") < (
        ids.index("generic/infinite-scroll")
        if "generic/infinite-scroll" in ids
        else len(ids)
    ), ids


def test_plain_static_page_does_not_get_the_signal() -> None:
    """反方向：没有内嵌载荷的普通页面不许被报成 embedded-json。"""
    html = (
        "<html><body><div class='list'>"
        + "".join(f"<div class='item'><h3>T{i}</h3></div>" for i in range(6))
        + "</div></body></html>"
    )
    report = _inspect(html)
    assert "embedded-json" not in report.structured_data, report.structured_data


def test_initial_state_assignment_is_also_detected() -> None:
    """Nuxt / Gatsby 的 `window.__INITIAL_STATE__` 同样算内嵌数据。"""
    html = (
        "<html><body><script>"
        'window.__INITIAL_STATE__={"list":{"items":[{"k":"a"}]}}'
        "</script></body></html>"
    )
    report = _inspect(html)
    assert "embedded-json" in report.structured_data, report.structured_data


# ── sitemap 声明（2026-11-27）────────────────────────────────────────────


def test_declared_sitemap_is_reported_and_recommended() -> None:
    """★ 站点声明了 sitemap ⇒ 全站发现最省事的入口。

    对「没识别出列表」的页面，这是最实用的一条下一步 —— 而不是让用户自己猜
    `sitemap.xml` 在哪。
    """
    html = (
        "<html><head>"
        '<link rel="sitemap" type="application/xml" href="/sitemap.xml">'
        "</head><body><div class='item'><h3>T1</h3></div></body></html>"
    )
    report = _inspect(html)
    assert "sitemap" in report.structured_data, report.structured_data
    ids = [item["id"] for item in report.recommendations]
    assert "protocols/sitemap" in ids, ids


def test_inline_sitemap_url_in_the_page_is_reported() -> None:
    """正文里出现 `.../sitemap.xml` 也算（很多站点把它写在页脚）。"""
    html = (
        "<html><body><footer>"
        '<a href="/sitemap_index.xml">Sitemap</a>'
        "</footer><div class='item'><h3>T1</h3></div></body></html>"
    )
    report = _inspect(html)
    assert "sitemap" in report.structured_data, report.structured_data


def test_plain_page_does_not_claim_a_sitemap() -> None:
    """反方向：没有 sitemap 声明的普通页面不许被报成有。"""
    html = "<html><body><div class='item'><h3>T1</h3></div></body></html>"
    report = _inspect(html)
    assert "sitemap" not in report.structured_data, report.structured_data
