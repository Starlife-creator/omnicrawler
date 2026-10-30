"""B-P2-9：PostgreSQL 分批 executemany；OpenSearch 无 helpers 环境回退逐条 index 契约保持。"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.core.models import CrawlRequest, ExtractedRecord
from omnicrawler.services.record_sinks import (
    _PG_BATCH_SIZE,
    OpenSearchRecordSink,
    PostgreSQLRecordSink,
)


class _Cursor:
    def __init__(self, connection: _FakeConnection) -> None:
        self.connection = connection

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, _sql: str, _params: tuple[object, ...] = ()) -> None:
        self.connection.executions.append(1)

    def executemany(self, _sql: str, values: list[tuple[object, ...]]) -> None:
        self.connection.batches.append(len(values))
        self.connection.values.extend(values)


class _FakeConnection:
    def __init__(self) -> None:
        self.executions: list[int] = []
        self.batches: list[int] = []
        self.values: list[tuple[object, ...]] = []
        self.committed = 0

    def cursor(self) -> _Cursor:
        return _Cursor(self)

    def commit(self) -> None:
        self.committed += 1

    def close(self) -> None:
        pass


def test_postgres_executemany_split_into_batches(tmp_path: Path) -> None:
    connection = _FakeConnection()
    sink = PostgreSQLRecordSink("", connection=connection)
    request = CrawlRequest("https://example.org/item")
    records = [
        ExtractedRecord(request.url, "item", {"title": f"A{i}"}, {"source": "test"})
        for i in range(_PG_BATCH_SIZE + 1)
    ]
    written = sink.write("run", request, records)

    assert written == len(records)
    # 两次 executemany：500 + 1，单次绑定元组不再整批驻留
    assert connection.batches == [_PG_BATCH_SIZE, 1]
    assert len(connection.values) == len(records)
    # 构造建表一次提交 + 写出整批结束后统一提交一次
    assert connection.committed == 2


class _SearchClient:
    def __init__(self) -> None:
        self.documents: list[dict[str, object]] = []

    def index(self, **document: object) -> None:
        self.documents.append(document)

    def close(self) -> None:
        pass


def test_opensearch_fallback_indexes_per_record_without_helpers(tmp_path: Path, monkeypatch) -> None:
    from omnicrawler.services import record_sinks

    monkeypatch.setattr(record_sinks, "_opensearch_helpers", lambda: None)
    client = _SearchClient()
    sink = OpenSearchRecordSink([], client=client)
    request = CrawlRequest("https://example.org/item")
    records = [
        ExtractedRecord(request.url, "item", {"title": "A"}, {"source": "test"}),
        ExtractedRecord(request.url, "item", {"title": "B"}, {"source": "test"}),
    ]
    assert sink.write("run", request, records) == 2
    assert len(client.documents) == 2
    assert client.documents[0]["body"]["data"] == {"title": "A"}


def test_opensearch_uses_helpers_bulk_and_stops_per_record_index(monkeypatch) -> None:
    from omnicrawler.services import record_sinks

    batches: list[list[dict[str, object]]] = []

    class _Helpers:
        @staticmethod
        def bulk(_client, actions, **_kwargs) -> None:
            batches.append(list(actions))

    monkeypatch.setattr(record_sinks, "_opensearch_helpers", lambda: _Helpers)
    client = _SearchClient()
    sink = OpenSearchRecordSink([], client=client)
    request = CrawlRequest("https://example.org/item")
    records = [
        ExtractedRecord(request.url, "item", {"title": str(index)}, {"source": "test"})
        for index in range(3)
    ]
    assert sink.write("run", request, records) == 3
    assert [len(batch) for batch in batches] == [3]
    assert client.documents == []
