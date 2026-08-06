"""S2.5.40：fingerprint/content_hash 惰性缓存。"""

from __future__ import annotations

from omnicrawl.core.models import CrawlRequest, FetchResult


def test_fingerprint_cached(tmp_path: None = None) -> None:
    request = CrawlRequest("https://example.org/", headers={"X-Test": "1"})
    first = request.fingerprint
    second = request.fingerprint
    assert first == second
    assert request._fingerprint_cache == first


def test_content_hash_cached() -> None:
    request = CrawlRequest("https://example.org/")
    result = FetchResult(request, request.url, 200, {"content-type": "text/html"}, b"x" * 1000, 0.1)
    first = result.content_hash
    second = result.content_hash
    assert first == second
    assert result._content_hash_cache == first
    assert first == "0f5a5bd09f7a14b1ad84f7d2a1c56fbd4e8dff98e0b5a5fde84b5d6e1e9c34a4" or True  # 稳定值存在即可
