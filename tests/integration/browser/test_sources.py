from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import yaml

from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.plugins.plugins import Registry
from omnicrawler.sources.sources import GenericSource, _with_query, register


def _config(tmp_path: Path, kind: str, source=None, crawl=None, download=None):
    value = {
        "project": {"name": "source-test", "workspace": str(tmp_path / "workspace")},
        "source": {"kind": kind, "seeds": ["https://example.org/start"]},
        "http": {"resolve_dns": False, "respect_robots": False},
        "crawl": crawl or {},
        "download": download or {},
    }
    if source:
        value["source"].update(source)
    path = tmp_path / f"{kind}.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return load_config(path)


def _result(url: str, body: bytes, *, request=None, content_type="text/html") -> FetchResult:
    request = request or CrawlRequest(url, meta={"root_url": url})
    return FetchResult(request, url, 200, {"content-type": content_type}, body, 0.1)


def test_seed_variants_rest_graphql_form_file_and_dict(tmp_path: Path) -> None:
    rest = GenericSource(
        _config(
            tmp_path,
            "rest",
            source={
                "method": "POST",
                "params": {"lang": "zh", "tag": ["a", "b"]},
                "payload": {"query": "demo"},
                "headers": {"X-Test": "yes"},
            },
        )
    ).seed()[0]
    assert rest.method == "POST"
    assert "lang=zh" in rest.url and rest.url.count("tag=") == 2
    assert json.loads(rest.body) == {"query": "demo"}
    assert rest.headers["Content-Type"] == "application/json"

    query_file = tmp_path / "query.graphql"
    query_file.write_text("query Demo { items { id } }", encoding="utf-8")
    graphql = GenericSource(
        _config(
            tmp_path,
            "graphql",
            source={"query_file": str(query_file), "variables": {"limit": 2}},
        )
    ).seed()[0]
    assert graphql.method == "POST"
    assert json.loads(graphql.body)["variables"] == {"limit": 2}

    form = GenericSource(
        _config(tmp_path, "form", source={"method": "POST", "fields": {"q": "policy"}})
    ).seed()[0]
    assert form.body == b"q=policy"
    assert form.headers["Content-Type"] == "application/x-www-form-urlencoded"

    file_request = GenericSource(_config(tmp_path, "file")).seed()[0]
    assert file_request.kind == "asset"
    browser_request = GenericSource(_config(tmp_path, "browser")).seed()[0]
    assert browser_request.render is True

    config = _config(
        tmp_path,
        "static_html",
        source={
            "seeds": [
                {
                    "url": "https://example.org/custom",
                    "method": "POST",
                    "headers": {"X-Custom": 1},
                    "payload": {"id": 3},
                    "kind": "asset",
                    "render": True,
                }
            ]
        },
    )
    custom = GenericSource(config).seed()[0]
    assert custom.kind == "asset" and custom.render and custom.headers["X-Custom"] == "1"


def test_page_pagination_get_and_post_body(tmp_path: Path) -> None:
    get_source = GenericSource(
        _config(
            tmp_path,
            "rest",
            source={"pagination": {"type": "page", "parameter": "page", "start": 2, "end": 6, "step": 2}},
        )
    )
    requests = get_source.seed()
    assert [item.meta["page"] for item in requests] == [2, 4, 6]
    assert all("page=" in item.url for item in requests)

    post_source = GenericSource(
        _config(
            tmp_path,
            "rest",
            source={
                "method": "POST",
                "payload": {"size": 10},
                "pagination": {
                    "type": "page",
                    "parameter": "offset",
                    "location": "body",
                    "start": 1,
                    "end": 2,
                },
            },
        )
    )
    requests = post_source.seed()
    assert [json.loads(item.body)["offset"] for item in requests] == [1, 2]


def test_s257_pagination_keeps_all_seeds(tmp_path: Path) -> None:
    multi = GenericSource(
        _config(
            tmp_path,
            "rest",
            source={
                "seeds": ["https://example.org/a", "https://example.org/b", "https://example.org/c"],
                "pagination": {"type": "page", "parameter": "page", "start": 1, "end": 3},
            },
        )
    )
    requests = multi.seed()
    urls = {item.url for item in requests}
    assert len(requests) == 9
    assert urls == {
        "https://example.org/a?page=1", "https://example.org/a?page=2", "https://example.org/a?page=3",
        "https://example.org/b?page=1", "https://example.org/b?page=2", "https://example.org/b?page=3",
        "https://example.org/c?page=1", "https://example.org/c?page=2", "https://example.org/c?page=3",
    }
    # 分页不覆盖原始 seed（无分页时保留全部）
    plain = GenericSource(
        _config(tmp_path, "rest", source={"seeds": ["https://example.org/a", "https://example.org/b"]})
    )
    assert len(plain.seed()) == 2


def test_html_discovery_attachments_media_pages_and_strategies(tmp_path: Path) -> None:
    html = b"""
    <html><body>
      <a href='/detail'>Policy detail</a>
      <a href='/download/report.pdf'>Report</a>
      <img src='/images/chart.png'>
      <a href='javascript:void(0)'>Bad</a>
    </body></html>
    """
    config = _config(
        tmp_path,
        "crawl",
        crawl={"focus_keywords": ["policy"], "strategy": "bfs"},
        download={"enabled": True, "media": True, "extensions": [".pdf"]},
    )
    requests = GenericSource(config).discover(_result("https://example.org/list", html))
    by_kind = {item.url: item for item in requests}
    assert by_kind["https://example.org/detail"].kind == "page"
    assert by_kind["https://example.org/detail"].priority == 1
    assert by_kind["https://example.org/download/report.pdf"].kind == "asset"
    assert by_kind["https://example.org/images/chart.png"].kind == "asset"

    parent = CrawlRequest("https://example.org/list", depth=3, meta={"root_url": "https://example.org"})
    result = _result("https://example.org/list", html, request=parent)
    dfs = GenericSource(_config(tmp_path, "crawl", crawl={"strategy": "dfs"}))
    assert dfs._child(result, "https://example.org/next", "page", "next").priority == 4
    random_source = GenericSource(_config(tmp_path, "crawl", crawl={"strategy": "random"}))
    with patch("random.random", return_value=0.42):
        assert random_source._child(result, "https://example.org/next", "page", "next").priority == 0.42

    static = GenericSource(_config(tmp_path, "static_html"))
    assert static.discover(_result("https://example.org", html)) == []


def test_sitemap_and_feed_discovery_deduplicate_and_reject_invalid_xml(tmp_path: Path) -> None:
    sitemap = GenericSource(_config(tmp_path, "sitemap"))
    xml = b"""<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>
      <url><loc>https://example.org/a</loc></url>
      <url><loc>https://example.org/a</loc></url>
      <url><loc>/b</loc></url>
    </urlset>"""
    requests = sitemap.discover(_result("https://example.org/sitemap.xml", xml, content_type="application/xml"))
    assert [item.url for item in requests] == ["https://example.org/a", "https://example.org/b"]
    assert sitemap.discover(_result("https://example.org", b"<broken", content_type="application/xml")) == []

    feed = GenericSource(_config(tmp_path, "feed"))
    atom = b"""<feed xmlns='http://www.w3.org/2005/Atom'>
      <entry><link href='https://example.org/one'/></entry>
      <entry><link>https://example.org/two</link></entry>
    </feed>"""
    assert [item.url for item in feed.discover(_result("https://example.org/feed", atom))] == [
        "https://example.org/one",
        "https://example.org/two",
    ]


def test_api_next_page_parameter_url_and_invalid_payloads(tmp_path: Path) -> None:
    source = GenericSource(
        _config(
            tmp_path,
            "rest",
            source={"pagination": {"next_path": "$.next", "parameter": "cursor"}},
        )
    )
    request = CrawlRequest("https://example.org/api?existing=1", headers={"X-Test": "1"})
    result = _result(
        "https://example.org/api?existing=1",
        b'{"next": "abc"}',
        request=request,
        content_type="application/json",
    )
    next_request = source.discover(result)[0]
    assert "existing=1" in next_request.url and "cursor=abc" in next_request.url
    assert next_request.headers == {"X-Test": "1"}

    url_source = GenericSource(
        _config(tmp_path, "rest", source={"pagination": {"next_path": "$.next"}})
    )
    result = _result(
        "https://example.org/api/page/1",
        b'{"next": "../page/2"}',
        content_type="application/json",
    )
    assert url_source.discover(result)[0].url == "https://example.org/api/page/2"

    assert url_source._discover_api_next(
        _result("https://example.org", b"not-json", content_type="application/json")
    ) == []
    assert url_source._discover_api_next(
        _result("https://example.org", b'{"next": null}', content_type="application/json")
    ) == []
    no_pagination = GenericSource(_config(tmp_path, "rest"))
    assert no_pagination._discover_api_next(result) == []


def test_query_helper_and_source_registration() -> None:
    url = _with_query("https://example.org/path?a=1#fragment", {"b": [2, 3], "empty": ""})
    assert url == "https://example.org/path?a=1&b=2&b=3&empty=#fragment"
    registry = Registry()
    register(registry)
    assert {"static_html", "rest", "browser", "websocket", "scrapy"}.issubset(registry.sources)
