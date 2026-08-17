"""S2.5.40：fingerprint/content_hash 惰性缓存。"""

from __future__ import annotations

import hashlib

from omnicrawler.core.models import CrawlRequest, FetchResult


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
    # 哈希是确定性函数：相同内容必须产生相同摘要（仅对 body 哈希，见 models.py）
    assert first == hashlib.sha256(b"x" * 1000).hexdigest()
