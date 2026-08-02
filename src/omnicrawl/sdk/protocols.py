"""Structural extension protocols for the public SDK preview."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol

from ..core.models import CrawlRequest, ExtractedRecord, FetchResult


class Source(Protocol):
    """Protocol for seed-URL and request-generation extensions.

    Implementations produce the initial set of :class:`CrawlRequest` objects
    that feed the pipeline's ``fetch`` stage.  Built-in sources include
    static URL lists, sitemap parsers, and API discovery walkers.
    """

    def requests(self) -> Iterable[CrawlRequest]: ...


class Fetcher(Protocol):
    """Protocol for network transport extensions.

    Implementations retrieve a single :class:`CrawlRequest` and return a
    :class:`FetchResult`.  Built-in fetchers cover HTTP (sync/async),
    Playwright/Selenium browser rendering, and WebSocket streams.
    """

    def fetch(self, request: CrawlRequest) -> FetchResult: ...


class Extractor(Protocol):
    """Protocol for content-parsing extensions.

    Implementations consume a :class:`FetchResult` and yield
    :class:`ExtractedRecord` objects.  Built-in extractors cover CSS/XPath
    HTML parsing, JSON API parsing, and PDF text extraction.
    """

    def extract(self, result: FetchResult) -> Iterable[ExtractedRecord]: ...


class Processor(Protocol):
    """Protocol for record-level transformation extensions.

    Implementations receive a single :class:`ExtractedRecord` and return a
    (possibly modified) :class:`ExtractedRecord`.  Use processors for
    field-level cleaning, normalisation, PII redaction, or enrichment.
    """

    def process(self, record: ExtractedRecord) -> ExtractedRecord: ...


class Exporter(Protocol):
    """Protocol for output-format extensions.

    Implementations consume an iterable of :class:`ExtractedRecord` and an
    options dict, returning a result dict with format-specific metadata.
    Built-in exporters cover CSV, JSON/JSONL, SQLite, DuckDB, and S3.
    """

    def export(self, records: Iterable[ExtractedRecord], options: dict[str, Any]) -> dict[str, Any]: ...


class CredentialProvider(Protocol):
    """Protocol for credential-injection extensions.

    Implementations receive a :class:`CrawlRequest` and return a new
    :class:`CrawlRequest` with credentials attached (headers, cookies,
    query parameters, or request body fields).  Use this to integrate
    with secret managers like HashiCorp Vault or AWS Secrets Manager.
    """

    def prepare(self, request: CrawlRequest) -> CrawlRequest: ...

