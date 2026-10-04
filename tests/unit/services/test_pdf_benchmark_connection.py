from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest

from omnicrawler.services import pdf_quality_benchmark as benchmark


def test_observation_connection_closes_before_return(tmp_path, monkeypatch):
    project = tmp_path / "sample"
    (project / "work").mkdir(parents=True)
    path = project / "work/pipeline.sqlite3"
    original = sqlite3.connect
    with closing(original(path)) as connection:
        connection.executescript("CREATE TABLE records(record_id INTEGER, review_status TEXT); CREATE TABLE field_values(record_id INTEGER, field_name TEXT, raw_value TEXT, normalized_value TEXT, page_no INTEGER, evidence TEXT, confidence REAL);")
    opened = []
    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        opened.append(connection)
        return connection
    monkeypatch.setattr(benchmark.sqlite3, "connect", connect)
    try:
        assert benchmark._observations(project, benchmark.PDF_CASES[0]) == []
        assert len(opened) == 1
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            opened[0].execute("SELECT 1")
        path.unlink()
    finally:
        for connection in opened:
            connection.close()
