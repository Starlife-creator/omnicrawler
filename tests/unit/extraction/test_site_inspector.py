from __future__ import annotations

from omnicrawl.core.models import CrawlRequest, FetchResult
from omnicrawl.sources.site_inspector import inspect_result
from omnicrawl.templates.template_catalog import bundled_template_catalog


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
