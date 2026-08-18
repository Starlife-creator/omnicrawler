"""双层去重过滤器 — URL 规范化去重 + SimHash 内容相似去重。

对齐 Helios 双层去重机制：
  第一层：URL 规范化（剥离 tracking 参数、统一大小写、排序查询参数）→ 同一 URL 不重复请求
  第二层：内容指纹（SimHash + 海明距离）→ 不同 URL 返回相同内容时只保留一条

复用 ``quality.data_intelligence.simhash`` 作为内容指纹实现，
本模块只负责 URL 规范层与去重容器（内存集合 + 有界容量）。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..quality.data_intelligence import hamming_distance, simhash

# 常见 tracking 参数：这些参数不影响页面内容，仅用于归因统计
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "gclid", "fbclid", "dclid", "gbraid", "wbraid", "msclkid", "yclid",
    "ref", "spm", "from", "source", "scm", "vd_source",
}

# SimHash 内容去重阈值（海明距离，越小越严格）。
# 实测：300 token 文本替换 1 词 → 距离约 4；替换 2 词 → 约 12；完全不同 → 约 31。
DEFAULT_HAMMING_THRESHOLD = 8
# 每个 URL 集合/内容集合的最大容量（防内存无限增长）
MAX_SEEN_URLS = 100_000
MAX_SEEN_CONTENT = 20_000


def strip_tracking_params(url: str) -> str:
    """剥离 URL 中的 tracking 参数，保留其余查询参数。

    Args:
        url: 原始 URL。

    Returns:
        剥离 tracking 参数后的 URL（未修改的部分保持原样）。
    """
    parts = urlsplit(url)
    if not parts.query:
        return url
    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key.casefold() not in _TRACKING_PARAMS
    ]
    if not kept:
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", parts.fragment))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))


def canonical_url(url: str) -> str:
    """规范化 URL：小写 scheme/host、剥离 tracking 参数、排序查询参数。

    Args:
        url: 原始 URL。

    Returns:
        规范化 URL。
    """
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.netloc:
        return url
    stripped = strip_tracking_params(url)
    parts = urlsplit(stripped)
    hostname = (parts.hostname or "").lower()
    port = parts.port
    default_port = (parts.scheme.lower() == "http" and port == 80) or (
        parts.scheme.lower() == "https" and port == 443
    )
    host = hostname if not port or default_port else f"{hostname}:{port}"
    query = urlencode(sorted(parse_qsl(parts.query, keep_blank_values=True)))
    return urlunsplit((parts.scheme.lower(), host, parts.path or "/", query, parts.fragment))


def _is_duplicate(hash_value: int, buckets: dict[tuple[int, int], list[tuple[int, object]]], threshold: int) -> bool:
    """SimHash 分桶查找：任意一桶中已有哈希与当前哈希距离 <= 阈值即视为重复。"""
    for band in range(4):
        band_value = (hash_value >> (band * 16)) & 0xFFFF
        for previous_hash, _previous in buckets.get((band, band_value), []):
            if hamming_distance(hash_value, previous_hash) <= threshold:
                return True
    return False


@dataclass(slots=True)
class DualLayerDupeFilter:
    """双层去重过滤器（内存版，线程不安全，单采集任务内使用）。

    Args:
        hamming_threshold: 内容相似判定阈值（海明距离）。
        max_urls: URL 去重集合上限。
        max_content: 内容指纹集合上限。
    """

    hamming_threshold: int = DEFAULT_HAMMING_THRESHOLD
    max_urls: int = MAX_SEEN_URLS
    max_content: int = MAX_SEEN_CONTENT

    _seen_urls: set[str] = field(default_factory=set, init=False, repr=False)
    _content_buckets: dict[tuple[int, int], list[tuple[int, object]]] = field(
        default_factory=lambda: defaultdict(list), init=False, repr=False
    )
    _content_count: int = field(default=0, init=False, repr=False)

    def url_seen(self, url: str) -> bool:
        """第一层：URL 规范化去重。True 表示已见过（应跳过）。"""
        key = canonical_url(url)
        if key in self._seen_urls:
            return True
        if len(self._seen_urls) >= self.max_urls:
            return False
        self._seen_urls.add(key)
        return False

    def content_seen(self, text: str) -> bool:
        """第二层：内容指纹相似去重。True 表示与已见内容相似（应跳过）。"""
        if not text.strip():
            return False
        hash_value = simhash(text)
        if _is_duplicate(hash_value, self._content_buckets, self.hamming_threshold):
            return True
        if self._content_count >= self.max_content:
            return False
        for band in range(4):
            band_value = (hash_value >> (band * 16)) & 0xFFFF
            self._content_buckets[(band, band_value)].append((hash_value, None))
        self._content_count += 1
        return False

    def observe(self, url: str, text: str) -> None:
        """记录一条已处理结果（供后续判断去重）。"""
        self.url_seen(url)
        self.content_seen(text)

    def size(self) -> dict[str, int]:
        return {"urls": len(self._seen_urls), "content": self._content_count}
