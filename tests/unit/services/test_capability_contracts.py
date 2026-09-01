"""Shared behavioral and structural contracts for replaceable capabilities."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from omnicrawler.core.models import CrawlRequest, ExtractedRecord
from omnicrawler.fetching.browser_fetcher import (
    BrowserEngine,
    PlaywrightAdapter,
    SeleniumAdapter,
)
from omnicrawler.pdfx.ocr import OCRBackend, PaddleStructureBackend, TesseractBackend
from omnicrawler.services.record_sinks import (
    OpenSearchRecordSink,
    PostgreSQLRecordSink,
    RecordSink,
)
from omnicrawler.services.storage_backends import LocalObjectStore, ObjectStore, S3ObjectStore


class _Body:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return self.payload


class _S3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, *, Bucket: str, Key: str, Body: bytes, **_options) -> None:
        self.objects[(Bucket, Key)] = Body

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, _Body]:
        return {"Body": _Body(self.objects[(Bucket, Key)])}

    def head_object(self, *, Bucket: str, Key: str) -> None:
        if (Bucket, Key) not in self.objects:
            error = RuntimeError("not found")
            error.response = {"ResponseMetadata": {"HTTPStatusCode": 404}}  # type: ignore[attr-defined]
            raise error


def _assert_object_store_contract(store: ObjectStore) -> None:
    assert isinstance(store, ObjectStore)
    stored = store.put("nested/result.txt", b"payload", content_type="text/plain")
    assert stored.key == "nested/result.txt"
    assert store.exists("nested/result.txt") is True
    assert store.get("nested/result.txt") == b"payload"
    assert store.exists("missing.txt") is False
    with pytest.raises(ValueError):
        store.put("../escape.txt", b"blocked")


def test_local_object_store_contract(tmp_path: Path) -> None:
    _assert_object_store_contract(LocalObjectStore(tmp_path / "objects"))


def test_s3_object_store_contract() -> None:
    _assert_object_store_contract(S3ObjectStore("bucket", prefix="runs", client=_S3Client()))


def test_record_sink_adapters_satisfy_public_shape() -> None:
    class _Sink:
        def write(
            self,
            run_id: str,
            request: CrawlRequest,
            records: list[ExtractedRecord],
        ) -> int:
            return len(records)

        def close(self) -> None:
            return None

    sink = _Sink()
    assert isinstance(sink, RecordSink)
    assert sink.write("run", CrawlRequest("https://example.com"), []) == 0
    assert isinstance(object.__new__(PostgreSQLRecordSink), RecordSink)
    assert isinstance(object.__new__(OpenSearchRecordSink), RecordSink)


def test_ocr_adapters_satisfy_public_shape() -> None:
    class _OCR:
        def recognize(self, png_bytes: bytes) -> tuple[str, float | None]:
            assert io.BytesIO(png_bytes).read() == b"png"
            return "text", 0.9

    backend = _OCR()
    assert isinstance(backend, OCRBackend)
    assert backend.recognize(b"png") == ("text", 0.9)
    assert isinstance(object.__new__(PaddleStructureBackend), OCRBackend)
    assert isinstance(object.__new__(TesseractBackend), OCRBackend)


def test_browser_adapters_satisfy_public_shape_without_loading_runtimes() -> None:
    assert isinstance(PlaywrightAdapter(object()), BrowserEngine)
    assert isinstance(object.__new__(SeleniumAdapter), BrowserEngine)
