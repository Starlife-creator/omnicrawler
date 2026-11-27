"""`GenericSource` 的两条**发现**路径（2026-11-27）。

## 为什么补这一份

CI 覆盖率门禁里 `pipeline_http_client` 组（`http_client.py` + `sources.py`）的下限是
86.4%。`sources.py` 的 `_discover_xml`（sitemap / feed）与 `_discover_api_next`
（游标分页）在此之前**几乎没有测试**，而它们都有明确的错误分支（坏 XML、游标类型
不对、分页重复）。这些分支不测，等于把所有"不能确认已完整遍历"的保护都交给运气。

★ 这几条用例全部**离线**：直接构造 `FetchResult`，不联网。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicrawler.core.config import AppConfig
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.sources.sources import GenericSource


def _config(tmp_path: Path, source: dict) -> AppConfig:
    raw = {
        "project": {"name": "src", "workspace": str(tmp_path / "work")},
        "source": source,
        "crawl": {"max_pages": 10},
    }
    return AppConfig(tmp_path / "c.yaml", tmp_path, raw, tmp_path / "work")


def _result(url: str, body: bytes, content_type: str = "text/html; charset=utf-8") -> FetchResult:
    return FetchResult(
        CrawlRequest(url), url, 200, {"content-type": content_type}, body, 0.01,
    )


# ── sitemap ─────────────────────────────────────────────────────────────


def test_sitemap_loc_entries_become_requests(tmp_path: Path) -> None:
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://example.org/a</loc></url>"
        "<url><loc>https://example.org/b</loc></url>"
        "</urlset>"
    )
    source = GenericSource(_config(tmp_path, {"kind": "sitemap", "seeds": ["https://example.org/sitemap.xml"]}))
    found = source.discover(_result("https://example.org/sitemap.xml", xml.encode(), "application/xml"))
    urls = [item.url for item in found]
    assert urls == ["https://example.org/a", "https://example.org/b"], urls
    assert all(item.depth == 1 for item in found)


def test_malformed_sitemap_returns_empty_not_raises(tmp_path: Path) -> None:
    """★ 坏 XML 必须安静地返回空，而不是让整次运行崩掉。"""
    source = GenericSource(_config(tmp_path, {"kind": "sitemap", "seeds": ["https://example.org/sitemap.xml"]}))
    found = source.discover(_result("https://example.org/sitemap.xml", b"<urlset><url>", "application/xml"))
    assert found == []


def test_sitemap_with_xxe_is_refused(tmp_path: Path) -> None:
    """★ XXE 防护：带外部实体的 XML 必须被 `defusedxml` 拒绝（返回空而不是解析它）。"""
    evil = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE urlset [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
        "<urlset><url><loc>&xxe;</loc></url></urlset>"
    )
    source = GenericSource(_config(tmp_path, {"kind": "sitemap", "seeds": ["https://example.org/sitemap.xml"]}))
    found = source.discover(_result("https://example.org/sitemap.xml", evil.encode(), "application/xml"))
    assert found == []


# ── feed ────────────────────────────────────────────────────────────────


def test_atom_feed_links_become_requests(tmp_path: Path) -> None:
    xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<entry><link href='https://example.org/1'/></entry>"
        "<entry><link href='https://example.org/2'/></entry>"
        "</feed>"
    )
    source = GenericSource(_config(tmp_path, {"kind": "feed", "seeds": ["https://example.org/feed"]}))
    found = source.discover(_result("https://example.org/feed", xml.encode(), "application/atom+xml"))
    assert [item.url for item in found] == ["https://example.org/1", "https://example.org/2"]


def test_duplicate_feed_links_are_deduped(tmp_path: Path) -> None:
    xml = (
        '<feed xmlns="http://www.w3.org/2005/Atom">'
        "<entry><link href='https://example.org/same'/></entry>"
        "<entry><link href='https://example.org/same'/></entry>"
        "</feed>"
    )
    source = GenericSource(_config(tmp_path, {"kind": "feed", "seeds": ["https://example.org/feed"]}))
    found = source.discover(_result("https://example.org/feed", xml.encode(), "application/atom+xml"))
    assert len(found) == 1, [item.url for item in found]


# ── 游标分页 ────────────────────────────────────────────────────────────


def _api_source(tmp_path: Path, **pagination: object) -> GenericSource:
    return GenericSource(
        _config(
            tmp_path,
            {
                "kind": "rest",
                "seeds": ["https://example.org/api/items"],
                "pagination": {"next_path": "$.next", **pagination},
            },
        )
    )


def test_cursor_pagination_generates_the_next_request(tmp_path: Path) -> None:
    source = _api_source(tmp_path)
    body = json.dumps({"next": "cursor-2", "items": []}).encode()
    found = source.discover(_result("https://example.org/api/items", body, "application/json"))
    assert len(found) == 1, found
    assert found[0].depth == 1
    assert found[0].meta["_api_pagination_generated"] is True


def test_null_cursor_stops_without_raising(tmp_path: Path) -> None:
    source = _api_source(tmp_path)
    body = json.dumps({"next": None, "items": []}).encode()
    result = _result("https://example.org/api/items", body, "application/json")
    assert source.discover(result) == []
    assert result.meta["pagination_diagnostic"]["stop_reason"] == "no_next_value"


def test_non_scalar_cursor_is_rejected(tmp_path: Path) -> None:
    """★ 游标是对象/数组时**必须报错**，因为无法确认已完整遍历。"""
    source = _api_source(tmp_path)
    body = json.dumps({"next": {"a": 1}, "items": []}).encode()
    with pytest.raises(ValueError, match="标量"):
        source.discover(_result("https://example.org/api/items", body, "application/json"))


def test_invalid_json_body_marks_stop_reason(tmp_path: Path) -> None:
    source = _api_source(tmp_path)
    result = _result("https://example.org/api/items", b"not json", "application/json")
    assert source.discover(result) == []
    assert result.meta["pagination_diagnostic"]["stop_reason"] == "invalid_pagination_response"


def test_repeated_cursor_is_refused(tmp_path: Path) -> None:
    """★ 分页返回已访问过的游标时必须报错（否则会无限循环）。"""
    source = _api_source(tmp_path)
    request = CrawlRequest("https://example.org/api/items")
    body = json.dumps({"next": "same"}).encode()
    first = FetchResult(request, request.url, 200, {"content-type": "application/json"}, body, 0.01)
    children = source.discover(first)
    assert len(children) == 1
    # 第二条请求把第一条的指纹记进 _api_pagination_seen，再返回同一个游标
    second = FetchResult(
        children[0], children[0].url, 200, {"content-type": "application/json"}, body, 0.01,
    )
    with pytest.raises(ValueError, match="重复"):
        source.discover(second)
