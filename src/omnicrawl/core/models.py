from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class CrawlRequest:
    url: str
    method: str = "GET"
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes | None = None
    kind: str = "page"
    render: bool = False
    priority: float = 0.0
    depth: int = 0
    parent_url: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    # S2.5.40：指纹惰性缓存（请求对象在生命周期内不可变）
    _fingerprint_cache: str | None = field(default=None, init=False, repr=False)

    @property
    def fingerprint(self) -> str:
        if self._fingerprint_cache is not None:
            return self._fingerprint_cache
        override = str(self.meta.get("_fingerprint_override", ""))
        if len(override) == 64 and all(char in "0123456789abcdefABCDEF" for char in override):
            self._fingerprint_cache = override.casefold()
            return self._fingerprint_cache
        stable = json.dumps(
            {
                "method": self.method.upper(),
                "url": self.url,
                # S2.5.5：指纹含规范化 headers（多语言/多身份采集不再误去重）。
                # 顺序无关，仅参与 sha256 摘要，不泄露 header 值本身。
                "headers": {key: value for key, value in sorted(self.headers.items())},
                "body": hashlib.sha256(self.body or b"").hexdigest(),
                "kind": self.kind,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        self._fingerprint_cache = hashlib.sha256(stable.encode("utf-8")).hexdigest()
        return self._fingerprint_cache


@dataclass(slots=True)
class FetchResult:
    request: CrawlRequest
    final_url: str
    status: int
    headers: dict[str, str]
    body: bytes
    elapsed_seconds: float
    meta: dict[str, Any] = field(default_factory=dict)
    # S2.5.40：内容哈希惰性缓存（body 在结果生命周期内不可变）
    _content_hash_cache: str | None = field(default=None, init=False, repr=False)

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()

    @property
    def content_hash(self) -> str:
        if self._content_hash_cache is None:
            self._content_hash_cache = hashlib.sha256(self.body).hexdigest()
        return self._content_hash_cache


@dataclass(slots=True)
class ExtractedRecord:
    source_url: str
    record_type: str
    data: dict[str, Any]
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ProcessResult:
    records: list[ExtractedRecord] = field(default_factory=list)
    requests: list[CrawlRequest] = field(default_factory=list)
    artifact_path: str | None = None
