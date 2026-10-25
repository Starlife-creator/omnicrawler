"""A-P3-10：重试额度语义回归。

- ``claim`` 只登记 ``claimed_at`` / ``worker``，不递增 ``attempts``；
- ``attempts`` 仅在 :meth:`StateStore.mark_failed` 记录真实失败时 +1；
- 崩溃重放（``prepare_cycle`` 把在途项回 pending）不重复扣减重试额度。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from omnicrawler.core.models import CrawlRequest
from omnicrawler.state import StateStore


def _row(state: StateStore, request: CrawlRequest) -> dict[str, object]:
    return state.rows(
        "SELECT status, attempts, claimed_at, worker FROM frontier WHERE fingerprint=?",
        (request.fingerprint,),
    )[0]


def test_claim_does_not_consume_retry_quota() -> None:
    with tempfile.TemporaryDirectory() as temp:
        with StateStore(Path(temp) / "state.sqlite3") as state:
            request = CrawlRequest("https://example.com/quota")
            state.enqueue(request)
            state.claim(1, worker="w-1")
            row = _row(state, request)
            assert row["status"] == "in_progress"
            assert int(row["attempts"]) == 0
            assert row["worker"] == "w-1"
            assert row["claimed_at"]


def test_crash_replay_does_not_consume_retry_quota() -> None:
    with tempfile.TemporaryDirectory() as temp:
        with StateStore(Path(temp) / "state.sqlite3") as state:
            request = CrawlRequest("https://example.com/crash")
            state.enqueue(request)
            # 第一次真实失败：attempts 0 → 1，回 pending 等待重试。
            state.claim(1, worker="w-1")
            state.mark_failed(request, RuntimeError("boom"), max_attempts=3, retryable=True)
            assert int(_row(state, request)["attempts"]) == 1

            # 模拟崩溃：在途/重试中的请求被 prepare_cycle 拉回 pending，
            # 重放后重试额度不得被再次扣减。
            state.conn.execute(
                "UPDATE frontier SET status='in_progress' WHERE fingerprint=?",
                (request.fingerprint,),
            )
            state.prepare_cycle()
            row = _row(state, request)
            assert row["status"] == "pending"
            assert int(row["attempts"]) == 1

            # 崩溃后重新认领，额度仍为 1；再次真实失败才递增到 2。
            state.claim(1, worker="w-2")
            assert int(_row(state, request)["attempts"]) == 1
            state.mark_failed(request, RuntimeError("again"), max_attempts=3, retryable=True)
            assert int(_row(state, request)["attempts"]) == 2


def test_mark_failed_marks_failed_after_budget_exhausted() -> None:
    with tempfile.TemporaryDirectory() as temp:
        with StateStore(Path(temp) / "state.sqlite3") as state:
            request = CrawlRequest("https://example.com/exhaust")
            state.enqueue(request)
            # max_attempts=1：无重试额度，第一次失败即进入终态 failed。
            state.claim(1)
            state.mark_failed(request, RuntimeError("x"), max_attempts=1, retryable=True)
            row = _row(state, request)
            assert row["status"] == "failed"
            assert int(row["attempts"]) == 1

            # 预算口径：max_attempts=2 = 最多 2 次尝试、允许 1 次重试。
            # 首次失败回 pending（重试），二次失败进入终态——额度只被真实失败消耗。
            retry = CrawlRequest("https://example.com/retry-budget")
            state.enqueue(retry)
            state.claim(1)
            state.mark_failed(retry, RuntimeError("a"), max_attempts=2, retryable=True)
            assert _row(state, retry)["status"] == "pending"
            state.claim(1)
            state.mark_failed(retry, RuntimeError("b"), max_attempts=2, retryable=True)
            row = _row(state, retry)
            assert row["status"] == "failed"
            assert int(row["attempts"]) == 2
