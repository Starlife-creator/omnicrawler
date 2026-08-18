from __future__ import annotations

import hashlib
import json
import re
import threading
import urllib.parse
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..core.config import AppConfig
from ..core.models import CrawlRequest, ExtractedRecord
from ..core.utils import utcnow
from ..security.egress import EgressBroker


class RecordSink(Protocol):
    def write(self, run_id: str, request: CrawlRequest, records: list[ExtractedRecord]) -> int: ...
    def close(self) -> None: ...


class PostgreSQLRecordSink:
    """Optional PostgreSQL JSONB mirror; SQLite remains the local recovery source."""

    def __init__(
        self,
        dsn: str,
        table: str = "omnicrawler_records",
        *,
        connection: Any = None,
        egress: EgressBroker | None = None,
    ) -> None:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", table):
            raise ValueError(f"Unsafe PostgreSQL table name: {table}")
        if connection is None:
            if not dsn.strip():
                raise ValueError("PostgreSQL DSN is required")
            try:
                import psycopg
                from psycopg.conninfo import conninfo_to_dict
            except ImportError as exc:
                raise RuntimeError(
                    "PostgreSQL storage requires: pip install omnicrawler-platform[postgresql]"
                ) from exc
            info = conninfo_to_dict(dsn)
            host = str(info.get("host") or "localhost")
            port = int(info.get("port") or 5432)
            endpoint = f"https://{host}:{port}/"
            boundary = egress.sdk_request(endpoint, transport="psycopg") if egress else nullcontext()
            with boundary:
                connection = psycopg.connect(dsn)
        else:
            endpoint = ""
        self.connection = connection
        self.table = table
        self.egress = egress
        self.endpoint = endpoint
        with self._sdk_request():
            with self.connection.cursor() as cursor:
                cursor.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table} (
                        record_id TEXT PRIMARY KEY,
                        run_id TEXT NOT NULL,
                        request_fingerprint TEXT NOT NULL,
                        source_url TEXT NOT NULL,
                        record_type TEXT NOT NULL,
                        data JSONB NOT NULL,
                        evidence JSONB NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL
                    )
                    """
                )
            self.connection.commit()

    def _sdk_request(self):
        return (
            self.egress.sdk_request(self.endpoint, transport="psycopg")
            if self.egress is not None and self.endpoint
            else nullcontext()
        )

    def write(self, run_id: str, request: CrawlRequest, records: list[ExtractedRecord]) -> int:
        now = utcnow()
        values = []
        for index, record in enumerate(records, 1):
            record_id = hashlib.sha256(
                f"{run_id}:{request.fingerprint}:{index}".encode()
            ).hexdigest()
            values.append(
                (
                    record_id, run_id, request.fingerprint, record.source_url, record.record_type,
                    json.dumps(record.data, ensure_ascii=False, default=str),
                    json.dumps(record.evidence, ensure_ascii=False, default=str), now,
                )
            )
        if not values:
            return 0
        with self._sdk_request():
            with self.connection.cursor() as cursor:
                cursor.executemany(
                    f"""
                    INSERT INTO {self.table}(
                        record_id, run_id, request_fingerprint, source_url, record_type,
                        data, evidence, created_at
                    ) VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
                    ON CONFLICT(record_id) DO UPDATE SET
                        data=excluded.data, evidence=excluded.evidence
                    """,
                    values,
                )
            self.connection.commit()
        return len(values)

    def close(self) -> None:
        self.connection.close()


class OpenSearchRecordSink:
    def __init__(
        self,
        hosts: str | list[str],
        index: str = "omnicrawler-records",
        *,
        client: Any = None,
        client_options: dict[str, Any] | None = None,
        egress: EgressBroker | None = None,
    ) -> None:
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,254}", index):
            raise ValueError(f"Unsafe OpenSearch index name: {index}")
        if client is None:
            try:
                from opensearchpy import OpenSearch
            except ImportError as exc:
                raise RuntimeError(
                    "OpenSearch storage requires: pip install omnicrawler-platform[search]"
                ) from exc
            normalized = [hosts] if isinstance(hosts, str) else hosts
            if not normalized:
                raise ValueError("OpenSearch hosts are required")
            client = OpenSearch(hosts=normalized, **(client_options or {}))
        self.client = client
        self.index = index
        self.egress = egress
        raw_hosts = [hosts] if isinstance(hosts, str) else hosts
        self.endpoints = [self._endpoint(item) for item in raw_hosts] if raw_hosts else []
        if self.egress is not None:
            for endpoint in self.endpoints:
                self.egress.authorize(endpoint, purpose="storage", count_request=False)

    @staticmethod
    def _endpoint(value: Any) -> str:
        if isinstance(value, dict):
            scheme = "https" if value.get("use_ssl", True) else "http"
            return f"{scheme}://{value.get('host', '')}:{int(value.get('port', 443 if scheme == 'https' else 80))}/"
        text = str(value).strip()
        return text if urllib.parse.urlsplit(text).scheme else f"https://{text}"

    def _sdk_request(self):
        if self.egress is None or not self.endpoints:
            return nullcontext()
        return self.egress.sdk_request(self.endpoints[0], transport="opensearch-py")

    def write(self, run_id: str, request: CrawlRequest, records: list[ExtractedRecord]) -> int:
        with self._sdk_request():
            for index, record in enumerate(records, 1):
                record_id = hashlib.sha256(
                    f"{run_id}:{request.fingerprint}:{index}".encode()
                ).hexdigest()
                self.client.index(
                    index=self.index,
                    id=record_id,
                    body={
                        "run_id": run_id,
                        "request_fingerprint": request.fingerprint,
                        "source_url": record.source_url,
                        "record_type": record.record_type,
                        "data": record.data,
                        "evidence": record.evidence,
                        "created_at": utcnow(),
                    },
                )
        return len(records)

    def close(self) -> None:
        close = getattr(self.client, "close", None)
        if callable(close):
            close()


@dataclass(slots=True)
class RecordSinkManager:
    sinks: list[tuple[str, RecordSink]] = field(default_factory=list)
    # S2.5.16：默认 fail_closed——sink 崩坏使运行失败，不再静默丢弃记录
    fail_open: bool = False
    max_errors: int = 200
    errors: list[dict[str, str]] = field(default_factory=list)
    _error_counts: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def _record_error(self, name: str, exc: Exception) -> None:
        error = {"sink": name, "error": f"{type(exc).__name__}: {exc}"}
        with self._lock:
            self._error_counts[name] = self._error_counts.get(name, 0) + 1
            if self.max_errors > 0:
                self.errors.append(error)
                overflow = len(self.errors) - self.max_errors
                if overflow > 0:
                    del self.errors[:overflow]

    def status(self) -> dict[str, Any]:
        """Return bounded recent failures plus complete per-sink failure counts."""
        with self._lock:
            return {
                "configured_sinks": [name for name, _sink in self.sinks],
                "fail_open": self.fail_open,
                "recent_errors": list(self.errors),
                "error_counts": dict(self._error_counts),
            }

    def write(self, run_id: str, request: CrawlRequest, records: list[ExtractedRecord]) -> None:
        for name, sink in self.sinks:
            try:
                sink.write(run_id, request, records)
            except Exception as exc:
                self._record_error(name, exc)
                if not self.fail_open:
                    raise

    def close(self) -> None:
        for name, sink in self.sinks:
            try:
                sink.close()
            except Exception as exc:
                self._record_error(name, exc)
                if not self.fail_open:
                    raise


def build_record_sink_manager(
    config: AppConfig,
    egress: EgressBroker | None = None,
) -> RecordSinkManager:
    settings = config.section("storage").get("records", {})
    if not isinstance(settings, dict):
        raise TypeError("storage.records must be a mapping")
    max_errors = int(settings.get("max_errors", 200))
    if max_errors < 0:
        raise ValueError("storage.records.max_errors不能为负数")
    manager = RecordSinkManager(
        # S2.5.16：默认 fail_closed——sink 崩坏使运行失败，不再静默丢记录
        fail_open=bool(settings.get("fail_open", False)), max_errors=max_errors
    )
    backends = settings.get("backends", [])
    if not isinstance(backends, list):
        raise TypeError("storage.records.backends must be a list")
    for item in backends:
        value = {"kind": item} if isinstance(item, str) else item
        if not isinstance(value, dict):
            raise TypeError("Record backend must be a name or mapping")
        kind = str(value.get("kind", "")).casefold()
        try:
            if kind in {"postgres", "postgresql"}:
                sink: RecordSink = PostgreSQLRecordSink(
                    str(value.get("dsn", "")),
                    str(value.get("table", "omnicrawler_records")),
                    egress=egress,
                )
            elif kind in {"opensearch", "search"}:
                sink = OpenSearchRecordSink(
                    value.get("hosts", []), str(value.get("index", "omnicrawler-records")),
                    client_options=value.get("client_options", {}),
                    egress=egress,
                )
            else:
                raise ValueError(f"Unsupported record storage backend: {kind}")
            manager.sinks.append((kind, sink))
        except Exception as exc:
            manager._record_error(kind or "unknown", exc)
            if not manager.fail_open:
                raise
    return manager
