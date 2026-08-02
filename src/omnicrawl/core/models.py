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

    @property
    def fingerprint(self) -> str:
        override = str(self.meta.get("_fingerprint_override", ""))
        if len(override) == 64 and all(char in "0123456789abcdefABCDEF" for char in override):
            return override.casefold()
        stable = json.dumps(
            {
                "method": self.method.upper(),
                "url": self.url,
                "body": hashlib.sha256(self.body or b"").hexdigest(),
                "kind": self.kind,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(stable.encode("utf-8")).hexdigest()


@dataclass(slots=True)
class FetchResult:
    request: CrawlRequest
    final_url: str
    status: int
    headers: dict[str, str]
    body: bytes
    elapsed_seconds: float
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "").split(";", 1)[0].strip().lower()

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.body).hexdigest()


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
