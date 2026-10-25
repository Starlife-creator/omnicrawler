"""A-P3-16：``StateStore.rows`` 动态 SQL 只读白名单回归（SELECT/WITH/PRAGMA 放行，
写操作 / DDL 一律拒绝），每次只读查询留痕审计。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.state import StateStore


def test_rows_allows_select_with_and_pragma(tmp_path: Path) -> None:
    with StateStore(tmp_path / "state.sqlite3") as store:
        assert store.rows("SELECT 1 AS one")[0]["one"] == 1
        assert store.rows("WITH x AS (SELECT 2 AS v) SELECT v FROM x")[0]["v"] == 2
        pragma = store.rows("PRAGMA table_info(frontier)")
        assert isinstance(pragma, list)


@pytest.mark.parametrize(
    "statement",
    (
        "UPDATE frontier SET status='done' WHERE fingerprint='x'",
        "DELETE FROM frontier",
        "INSERT INTO frontier(url) VALUES('https://example.org/')",
        "DROP TABLE frontier",
        "ALTER TABLE frontier ADD COLUMN z INT",
    ),
)
def test_rows_rejects_write_and_ddl_statements(tmp_path: Path, statement: str) -> None:
    with StateStore(tmp_path / "state.sqlite3") as store:
        with pytest.raises(ValueError, match="只读"):
            store.rows(statement)


def test_rows_readonly_query_records_audit_trail(tmp_path: Path) -> None:
    with StateStore(tmp_path / "state.sqlite3") as store:
        store.rows("SELECT 1")
        rows = store.conn.execute(
            "SELECT action FROM audit_events WHERE action=?", ("raw_rows_query",)
        ).fetchall()
        assert rows
