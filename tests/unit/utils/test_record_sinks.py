from contextlib import nullcontext
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from omnicrawl.core.config import load_config
from omnicrawl.core.models import CrawlRequest, ExtractedRecord
from omnicrawl.services.record_sinks import (
    OpenSearchRecordSink,
    PostgreSQLRecordSink,
    build_record_sink_manager,
)


class _Cursor:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def execute(self, sql):
        self.connection.statements.append(sql)

    def executemany(self, sql, values):
        self.connection.statements.append(sql)
        self.connection.values.extend(values)


class _Connection:
    def __init__(self):
        self.statements = []
        self.values = []
        self.closed = False

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        return None

    def close(self):
        self.closed = True


class _SearchClient:
    def __init__(self):
        self.documents = []
        self.closed = False

    def index(self, **document):
        self.documents.append(document)

    def close(self):
        self.closed = True


def test_postgresql_and_opensearch_contracts_with_injected_clients():
    request = CrawlRequest("https://example.org/item")
    records = [ExtractedRecord(request.url, "item", {"title": "A"}, {"source": "test"})]

    connection = _Connection()
    postgres = PostgreSQLRecordSink("", connection=connection)
    assert postgres.write("run", request, records) == 1
    assert len(connection.values) == 1
    assert connection.values[0][5] == '{"title": "A"}'
    postgres.close()
    assert connection.closed is True

    client = _SearchClient()
    search = OpenSearchRecordSink([], client=client)
    assert search.write("run", request, records) == 1
    assert client.documents[0]["body"]["data"] == {"title": "A"}
    search.close()
    assert client.closed is True


def test_optional_record_backend_fail_open_is_reported(tmp_path: Path):
    config_path = tmp_path / "project.yaml"
    config_path.write_text(
        "project: {name: sinks, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.com/]}\n"
        "storage:\n"
        "  records:\n"
        "    fail_open: true\n"
        "    backends: [{kind: unknown}]\n",
        encoding="utf-8",
    )
    manager = build_record_sink_manager(load_config(config_path))
    assert manager.sinks == []
    assert manager.errors[0]["sink"] == "unknown"


def test_opensearch_sdk_calls_are_preauthorized_and_audited():
    broker = MagicMock()
    broker.sdk_request.return_value = nullcontext()
    client = _SearchClient()
    sink = OpenSearchRecordSink(
        ["https://search.example.com"], client=client, egress=broker
    )
    request = CrawlRequest("https://source.example/item")
    sink.write("run", request, [ExtractedRecord(request.url, "item", {"id": 1})])
    broker.authorize.assert_called_once_with(
        "https://search.example.com", purpose="storage", count_request=False
    )
    broker.sdk_request.assert_called_once_with(
        "https://search.example.com", transport="opensearch-py"
    )


def test_opensearch_sink_fails_closed_when_egress_denies():
    """出网授权拒绝 → sink 构造即失败，SDK 索引永不调用（fail-closed）。"""
    from omnicrawl.security.egress import EgressDisabledError

    broker = MagicMock()
    broker.authorize.side_effect = EgressDisabledError("denied for test")
    client = _SearchClient()
    with pytest.raises(EgressDisabledError):
        OpenSearchRecordSink(
            ["https://search.example.com"], client=client, egress=broker
        )
    assert client.documents == []
