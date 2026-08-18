"""S2.5.38：retry_failed 分批处理（内存可控）。"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.core.models import CrawlRequest
from omnicrawler.state import StateStore


def test_retry_failed_batches_and_respects_limit(tmp_path: Path) -> None:
    with StateStore(tmp_path / "state.sqlite3") as state:
        for index in range(2500):
            request = CrawlRequest(f"https://example.org/failed-{index}")
            state.enqueue(request)
            state.mark_done(request.fingerprint, status="failed", error="err")
        assert state.retry_failed() == 2500
        pending = state.rows(
            "SELECT COUNT(*) AS n FROM frontier WHERE status='pending'"
        )[0]["n"]
        assert pending == 2500
        # limit 生效
        for index in range(500):
            request = CrawlRequest(f"https://example.org/failed-{index}")
            state.enqueue(request)
            state.mark_done(request.fingerprint, status="failed", error="err")
        assert state.retry_failed(limit=300) == 300
