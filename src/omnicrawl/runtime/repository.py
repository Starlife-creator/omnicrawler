from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..state import StateStore


@runtime_checkable
class RunRepository(Protocol):
    """Persistence port used by application services; SQLite is only one adapter."""

    def latest_run(self) -> dict[str, Any] | None:
        """Return the most recent run record, or ``None`` if no runs exist."""
        ...
    def stats(self, run_id: str | None = None) -> dict[str, Any]:
        """Return aggregate statistics for a run (or the latest run if omitted)."""
        ...
    def rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        """Execute a raw SQL query and return rows as dicts."""
        ...
    def retry_failed(self, limit: int | None = None) -> int:
        """Re-enqueue failed requests, returning the number re-queued."""
        ...
    def recover_incomplete_runs(self) -> list[str]:
        """Mark stalled runs as failed and return their IDs."""
        ...


class SQLiteRunRepository:
    # Deprecated: migrate to StateStore. Will be removed in 3.0.0.
    """Default repository adapter with an explicit lifecycle."""

    def __init__(self, path: Path) -> None:
        warnings.warn("Use StateStore instead", DeprecationWarning, stacklevel=2)
        self.store = StateStore(path)

    def __enter__(self) -> SQLiteRunRepository:
        return self

    def __exit__(self, *_args: Any) -> None:
        self.store.close()

    def latest_run(self) -> dict[str, Any] | None:
        return self.store.latest_run()

    def stats(self, run_id: str | None = None) -> dict[str, Any]:
        return self.store.stats(run_id)

    def rows(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return self.store.rows(sql, params)

    def retry_failed(self, limit: int | None = None) -> int:
        return self.store.retry_failed(limit)

    def recover_incomplete_runs(self) -> list[str]:
        return self.store.recover_incomplete_runs()
