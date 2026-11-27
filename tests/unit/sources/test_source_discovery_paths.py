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


# ── gzip 压缩的 sitemap（sitemaps.org 允许的形态）─────────────────────


def _sitemap_source(tmp_path: Path) -> GenericSource:
    return GenericSource(_config(tmp_path, {"kind": "sitemap", "seeds": ["https://example.org/sitemap.xml.gz"]}))


_SITEMAP_XML = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
    b"<url><loc>https://example.org/a</loc></url>"
    b"<url><loc>https://example.org/b</loc></url>"
    b"</urlset>"
)


def test_gzip_sitemap_is_decompressed(tmp_path: Path) -> None:
    """★ `sitemap.xml.gz` 是 sitemaps.org 明确允许的形态。

    实测缺陷（2026-11-27）：gzip 字节直接喂给 XML 解析 ⇒ 解析失败 ⇒ 返回 `[]`，
    既不报错也不告警 —— 用户只会觉得"这个站没内容"，而它其实有几十万条 URL。
    """
    import gzip

    source = _sitemap_source(tmp_path)
    found = source.discover(_result(
        "https://example.org/sitemap.xml.gz",
        gzip.compress(_SITEMAP_XML),
        "application/gzip",
    ))
    assert [item.url for item in found] == ["https://example.org/a", "https://example.org/b"]


def test_gzip_is_detected_by_magic_bytes_not_only_extension(tmp_path: Path) -> None:
    """★ CDN 常把 `.gz` 去掉、或用 `application/octet-stream` 返回 ⇒ 必须认魔数。"""
    import gzip

    source = GenericSource(_config(tmp_path, {"kind": "sitemap", "seeds": ["https://example.org/sitemap.xml"]}))
    found = source.discover(_result(
        "https://example.org/sitemap.xml",
        gzip.compress(_SITEMAP_XML),
        "application/octet-stream",  # 站点常这么返回
    ))
    assert len(found) == 2, [item.url for item in found]


def test_plain_xml_still_works(tmp_path: Path) -> None:
    """反方向：未压缩的 sitemap 行为不变。"""
    source = _sitemap_source(tmp_path)
    found = source.discover(_result("https://example.org/sitemap.xml.gz", _SITEMAP_XML, "application/xml"))
    assert len(found) == 2


def test_corrupt_gzip_falls_back_and_does_not_crash(tmp_path: Path) -> None:
    """解压失败时退回原始字节（后续解析自然失败），**不许抛异常**。"""
    source = _sitemap_source(tmp_path)
    found = source.discover(_result(
        "https://example.org/sitemap.xml.gz",
        b"\x1f\x8b" + b"not really gzip",
        "application/gzip",
    ))
    assert found == []


def test_gzip_bomb_is_rejected(tmp_path: Path, monkeypatch) -> None:
    """★ 压缩炸弹必须被**限流拒绝**，而不是把内存吃光。

    判据：上限被压到很小后，超大解压结果必须得到空列表；且真解压量不超过"上限+1"。
    """
    import gzip

    from omnicrawler.sources import sources as sources_module

    # ★ 夹具必须是**合法但超大**的 sitemap：如果只解压出垃圾字节，去掉上限判据后
    #   解析照样失败、测试照样绿 —— 那就是假守卫（第一版正是如此）。
    rows = "".join(
        f"<url><loc>https://example.org/{index}</loc></url>" for index in range(2000)
    )
    payload = ('<?xml version="1.0"?><urlset>' + rows + "</urlset>").encode()
    assert len(payload) > 64 * 10
    monkeypatch.setattr(sources_module, "_SITEMAP_MAX_DECOMPRESSED", 64)
    bomb = gzip.compress(payload)
    # ★ 判据必须打在**解压这一层**：如果只看 `discover()` 的返回值，去掉上限后
    #   截断的 XML 解析照样失败 ⇒ 返回空 ⇒ 测试绿 —— 那就是假守卫（已验证两次）。
    assert sources_module._xml_payload(_result("https://example.org/sitemap.xml.gz", bomb, "application/gzip")) == b""
    # 顺带确认整条发现路径也不会把它当成数据
    source = _sitemap_source(tmp_path)
    assert source.discover(_result("https://example.org/sitemap.xml.gz", bomb, "application/gzip")) == []


def test_gzip_sitemap_index_expands_to_subsitemaps(tmp_path: Path) -> None:
    """`.gz` 的 sitemap **索引**同样要能展开（大站常见三层）。"""
    import gzip

    index = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        b"<sitemap><loc>https://example.org/sitemap-1.xml.gz</loc></sitemap>"
        b"</sitemapindex>"
    )
    source = _sitemap_source(tmp_path)
    found = source.discover(_result("https://example.org/sitemap.xml.gz", gzip.compress(index), "application/gzip"))
    assert [item.url for item in found] == ["https://example.org/sitemap-1.xml.gz"]


def test_leading_whitespace_and_bom_before_the_declaration(tmp_path: Path) -> None:
    """★ 实测缺陷（2026-11-27）：`<?xml?>` 前面多一个换行就会让 Expat 报

        XML or text declaration not at start of entity

    ⇒ 整个 sitemap 解析失败 ⇒ 返回 `[]`。真实站点真的这么发：
    `web-scraping.dev/sitemap.xml` 的第一个字节就是 `\n`。

    规范要求声明在文件最开头，但**我们能容忍的就不该让用户承担**。
    """
    source = _sitemap_source(tmp_path)
    for prefix in (b"\n", b"\r\n", b"  \n\t", b"\xef\xbb\xbf", b"\xef\xbb\xbf\n"):
        body = prefix + _SITEMAP_XML
        found = source.discover(_result("https://example.org/sitemap.xml.gz", body, "application/xml"))
        assert len(found) == 2, (prefix, [item.url for item in found])


def test_real_world_sitemap_shape_parses(tmp_path: Path) -> None:
    """真实形态：声明前有换行 + `xmlns:image` 扩展命名空间 + lastmod 等子元素。"""
    body = (
        b'\n<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        b'xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">'
        b"<url><loc>https://example.org/products</loc>"
        b"<lastmod>2023-05-20T00:00:00.000Z</lastmod>"
        b"<changefreq>weekly</changefreq><priority>0.8</priority></url>"
        b"</urlset>"
    )
    source = _sitemap_source(tmp_path)
    found = source.discover(_result("https://example.org/sitemap.xml.gz", body, "text/html; charset=utf-8"))
    assert [item.url for item in found] == ["https://example.org/products"]
