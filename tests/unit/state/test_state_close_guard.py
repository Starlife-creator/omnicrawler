"""S2.5.42：StateStore 关闭防护 + rows 只读白名单 + force 保留 attempts。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.core.models import CrawlRequest
from omnicrawler.state import StateStore


def test_operations_after_close_raise_controlled_error(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "state.sqlite3")
    store.close()
    with pytest.raises(RuntimeError, match="已关闭"):
        store.rows("SELECT 1")
    store.close()  # 幂等


def test_rows_rejects_write_statements(tmp_path: Path) -> None:
    with StateStore(tmp_path / "state.sqlite3") as store:
        with pytest.raises(ValueError, match="只读"):
            store.rows("DELETE FROM frontier")
        with pytest.raises(ValueError, match="只读"):
            store.rows("  INSERT INTO frontier(x) VALUES(1)")
        assert store.rows("SELECT 1 AS one")[0]["one"] == 1


def test_enqueue_force_preserves_attempts(tmp_path: Path) -> None:
    with StateStore(tmp_path / "state.sqlite3") as store:
        request = CrawlRequest("https://example.org/")
        store.enqueue(request)
        store.mark_done(request.fingerprint, status="failed", error="boom")
        store.conn.execute(
            "UPDATE frontier SET attempts=7 WHERE fingerprint=?", (request.fingerprint,)
        )
        store.enqueue(request, force=True)
        attempts = store.rows(
            "SELECT attempts FROM frontier WHERE fingerprint=?", (request.fingerprint,)
        )[0]["attempts"]
        assert attempts == 7  # force 重入队不重置重试计数
