"""Unit tests for the runtime repository port and SQLite adapter.

Covers:
- RunRepository protocol conformance
- SQLiteRunRepository lifecycle (context manager)
- Delegation to StateStore for all methods
- DeprecationWarning on construction
"""
from __future__ import annotations

import warnings
from pathlib import Path

import pytest

from omnicrawl.runtime.repository import RunRepository, SQLiteRunRepository
from omnicrawl.state import StateStore


def test_repository_port_and_sqlite_adapter(tmp_path: Path) -> None:
    """SQLiteRunRepository implements the RunRepository protocol."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        with SQLiteRunRepository(tmp_path / "state.sqlite3") as repository:
            assert isinstance(repository, RunRepository)
            assert repository.latest_run() is None
            assert repository.stats()["frontier"] == {}


def test_deprecation_warning(tmp_path: Path) -> None:
    """Constructing SQLiteRunRepository emits a DeprecationWarning."""
    with pytest.warns(DeprecationWarning, match="Use StateStore instead"):
        repo = SQLiteRunRepository(tmp_path / "state.sqlite3")
        repo.store.close()


def test_context_manager_lifecycle(tmp_path: Path) -> None:
    """SQLiteRunRepository supports with-statement and closes store on exit."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        with SQLiteRunRepository(tmp_path / "state.sqlite3") as repo:
            assert repo.store is not None
        # After exiting, store should be closed — calling close again is safe
        repo.store.close()


def test_delegates_to_state_store(tmp_path: Path) -> None:
    """All repository methods delegate to the underlying StateStore."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        with SQLiteRunRepository(tmp_path / "state.sqlite3") as repo:
            # stats delegates
            stats = repo.stats()
            assert "frontier" in stats
            # latest_run delegates
            assert repo.latest_run() is None
            # retry_failed delegates (no failed items)
            assert repo.retry_failed() == 0
            # recover_incomplete_runs delegates (no incomplete runs)
            assert repo.recover_incomplete_runs() == []
            # rows delegates
            rows = repo.rows("SELECT 1 as val")
            assert rows == [{"val": 1}]


def test_run_repository_protocol_methods() -> None:
    """RunRepository protocol declares all expected methods."""
    expected = {"latest_run", "stats", "rows", "retry_failed", "recover_incomplete_runs"}
    actual = set(dir(RunRepository))
    assert expected.issubset(actual)


def test_state_store_direct_usage(tmp_path: Path) -> None:
    """StateStore works directly without the deprecated wrapper."""
    store = StateStore(tmp_path / "state.sqlite3")
    try:
        assert store.latest_run() is None
        assert store.stats()["frontier"] == {}
        assert store.retry_failed() == 0
    finally:
        store.close()
