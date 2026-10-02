from __future__ import annotations

from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.sources.site_inspector import inspect_result
from omnicrawler.templates.template_catalog import bundled_template_catalog


def test_inspector_detects_cms_page_type_and_assets() -> None:
    html = b'''<html><head><meta property="og:title" content="Story">
      <script type="application/ld+json">{"@type":"NewsArticle"}</script></head>
      <body class="wp-content"><article><a href="/file.pdf">PDF</a></article>
      <script>window.wpApiSettings={root:'/wp-json/'}; fetch('/api/items')</script></body></html>'''
    request = CrawlRequest("https://example.org/story")
    result = FetchResult(request, request.url, 200, {"content-type": "text/html"}, html, 0.01)

    report = inspect_result(result, bundled_template_catalog())

    assert report.page_type == "detail"
    assert "wordpress" in report.cms
    assert "json-ld" in report.structured_data
    assert "opengraph" in report.structured_data
    assert "fetch" in report.api_signals
    assert "pdf" in report.downloads
    assert report.recommendations


def test_inspector_reports_script_only_business_content_as_unconfirmed_dynamic() -> None:
    request = CrawlRequest("https://example.org/js/")
    body = b"<div id='list'></div><a href='/login'>Login</a><script>document.querySelector('#list').innerHTML='rows';</script>"
    result = FetchResult(request, request.url, 200, {"content-type": "text/html"}, body, .01)
    report = inspect_result(result, bundled_template_catalog())
    assert report.dynamic and report.browser_recommended
    assert "静态内容" in report.browser_reason
    assert not report.rendered


def test_inspector_reads_json_continuation_evidence() -> None:
    request = CrawlRequest("https://example.org/api")
    result = FetchResult(request, request.url, 200, {"content-type": "application/json"},
                         b'{"items":[{"id":1}],"next":"two"}', .01)
    report = inspect_result(result, bundled_template_catalog())
    assert "cursor-or-next-url" in report.pagination
    assert not report.browser_recommended


def test_render_failure_is_retained_as_inspection_error(monkeypatch) -> None:
    from omnicrawler.fetching import page_probe
    from omnicrawler.fetching.browser_fetcher import BrowserFetcher
    from omnicrawler.sources import site_inspector

    request = CrawlRequest("https://example.org/js/")
    result = FetchResult(request, request.url, 200, {"content-type": "text/html"},
                         b"<main></main><script>render()</script>", .01)
    monkeypatch.setattr(page_probe, "_guarded_fetch", lambda *a, **k: result)
    monkeypatch.setattr(page_probe.RobotsPolicy, "allowed", lambda *a: True)
    monkeypatch.setattr(BrowserFetcher, "fetch", lambda *a: (_ for _ in ()).throw(RuntimeError("renderer unavailable")))
    report = site_inspector.inspect_url(request.url, bundled_template_catalog())
    assert report.browser_recommended and not report.rendered
    assert "renderer unavailable" in report.analysis_error
