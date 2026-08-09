"""Tests for quality.dupe_filter — URL 规范化去重 + SimHash 内容去重."""

from __future__ import annotations

from omnicrawl.quality.dupe_filter import (
    DualLayerDupeFilter,
    canonical_url,
    strip_tracking_params,
)


class TestStripTrackingParams:
    def test_removes_known_tracking(self) -> None:
        url = "https://example.com/list?utm_source=news&utm_medium=email&page=2"
        assert strip_tracking_params(url) == "https://example.com/list?page=2"

    def test_no_query_unchanged(self) -> None:
        assert strip_tracking_params("https://example.com/a") == "https://example.com/a"

    def test_keeps_non_tracking(self) -> None:
        url = "https://example.com/search?q=python&ref=share"
        assert strip_tracking_params(url) == "https://example.com/search?q=python"

    def test_all_tracking_only_removes_query(self) -> None:
        url = "https://example.com/x?utm_source=a&gclid=b"
        assert strip_tracking_params(url) == "https://example.com/x"


class TestCanonicalUrl:
    def test_lowercases_host(self) -> None:
        assert canonical_url("HTTPS://Example.COM/Path") == "https://example.com/Path"

    def test_sorts_query_params(self) -> None:
        assert canonical_url("https://example.com/?b=2&a=1") == "https://example.com/?a=1&b=2"

    def test_drops_default_port(self) -> None:
        assert canonical_url("https://example.com:443/a") == "https://example.com/a"

    def test_tracking_params_stripped(self) -> None:
        assert canonical_url("https://example.com/a?utm_campaign=x&id=1") == "https://example.com/a?id=1"

    def test_invalid_scheme_unchanged(self) -> None:
        url = "ftp://example.com/a"
        assert canonical_url(url) == url


class TestDualLayerDupeFilter:
    def test_url_dedup(self) -> None:
        dupe = DualLayerDupeFilter()
        assert dupe.url_seen("https://example.com/a?utm_source=x") is False
        assert dupe.url_seen("https://example.com/a") is True

    def test_url_dedup_query_order_insensitive(self) -> None:
        dupe = DualLayerDupeFilter()
        assert dupe.url_seen("https://example.com/?b=2&a=1") is False
        assert dupe.url_seen("https://example.com/?a=1&b=2") is True

    def test_url_dedup_port_variants(self) -> None:
        dupe = DualLayerDupeFilter()
        assert dupe.url_seen("https://example.com:443/x") is False
        assert dupe.url_seen("https://example.com/x") is True

    def test_content_dedup_similar(self) -> None:
        dupe = DualLayerDupeFilter(hamming_threshold=8)
        base = ("天气 晴朗 气温 回升 适合 出行 美好 生活 快乐 工作 " * 30).strip()
        assert dupe.content_seen(base) is False
        variant = base.replace("适合", "适宜", 1)
        assert dupe.content_seen(variant) is True

    def test_content_dedup_multiple_word_change_not_duplicate(self) -> None:
        # 2 词替换（距离约 12）超过阈值 8 → 不判重，避免过度去重
        dupe = DualLayerDupeFilter(hamming_threshold=8)
        base = ("天气 晴朗 气温 回升 适合 出行 美好 生活 快乐 工作 " * 30).strip()
        assert dupe.content_seen(base) is False
        variant = base.replace("适合 出行", "适宜 出门", 1)
        assert dupe.content_seen(variant) is False

    def test_content_dedup_distinct(self) -> None:
        dupe = DualLayerDupeFilter(hamming_threshold=8)
        assert dupe.content_seen("完全不同的第一篇文章内容") is False
        assert dupe.content_seen("完全不同的第二篇文章内容") is False

    def test_empty_content_never_duplicate(self) -> None:
        dupe = DualLayerDupeFilter()
        assert dupe.content_seen("") is False
        assert dupe.content_seen("   ") is False

    def test_observe_and_size(self) -> None:
        dupe = DualLayerDupeFilter()
        dupe.observe("https://example.com/a", "第一条内容")
        dupe.observe("https://example.com/b", "第二条内容")
        assert dupe.size()["urls"] == 2
        assert dupe.size()["content"] == 2
