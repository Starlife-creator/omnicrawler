"""StateStore 的「任务队列」域 —— 从 state_store.py 抽出的 Mixin（P1-3 第一批）。

覆盖 BFS/DFS 队列的周期准备、失败重试、入队、认领与终态标记：
``prepare_cycle`` / ``retry_failed`` / ``enqueue`` / ``claim`` / ``_row_to_request`` /
``mark_done`` / ``mark_failed`` / ``pending_count``，以及 ``_CLAIM_ORDER`` 排序表。

以 Mixin 形式保留 ``self`` 语义（宿主 ``conn`` / ``_lock`` 不变），调用点零改动。
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..core.models import CrawlRequest
from ..core.utils import json_text, redact_headers, utcnow


class QueueMixin:
    """任务队列域：入队 / 认领 / 终态标记。"""

    # ---- 宿主契约：实例属性（由 StateStore.__init__ 建立）----
    conn: sqlite3.Connection
    _lock: Any

    _CLAIM_ORDER: dict[str, str] = {
        "bfs": "priority DESC, depth ASC, id ASC",
        "dfs": "depth DESC, priority DESC, id DESC",
        "priority": "priority DESC, depth ASC, id ASC",
        "random": "RANDOM()",
    }
    def prepare_cycle(self, *, reset_all: bool = False) -> None:
        with self._lock, self.conn:
            self.conn.execute("UPDATE frontier SET status='pending', updated_at=? WHERE status='in_progress'", (utcnow(),))
            if reset_all:
                self.conn.execute(
                    "UPDATE frontier SET status='pending', attempts=0, last_error=NULL, updated_at=? WHERE status IN ('done','failed','blocked')",
                    (utcnow(),),
                )

    def retry_failed(self, limit: int | None = None) -> int:
        """Move dead-letter frontier entries back to pending without resetting completed work.

        S2.5.38：分批拉取（每批 1000），大规模失败场景内存可控。
        """
        total = 0
        batch = 1000
        while True:
            with self._lock, self.conn:
                remaining = None if limit is None else max(0, limit - total)
                if remaining == 0:
                    break
                want = batch if remaining is None else min(batch, remaining)
                rows = self.conn.execute(
                    "SELECT fingerprint FROM frontier WHERE status='failed' ORDER BY updated_at LIMIT ?",
                    (want,),
                ).fetchall()
                if not rows:
                    break
                self.conn.executemany(
                    "UPDATE frontier SET status='pending', attempts=0, last_error=NULL, updated_at=? WHERE fingerprint=?",
                    [(utcnow(), row["fingerprint"]) for row in rows],
                )
            total += len(rows)
            if len(rows) < want:
                break
        return total

    def enqueue(self, request: CrawlRequest, *, force: bool = False) -> bool:
        now = utcnow()
        values = (
            request.fingerprint, request.url, request.method.upper(), json_text(redact_headers(request.headers)),
            request.body, request.kind, int(request.render), request.priority, request.depth,
            request.parent_url, json_text(request.meta), now, now,
        )
        with self._lock, self.conn:
            cursor = self.conn.execute(
                """
                INSERT OR IGNORE INTO frontier(
                    fingerprint, url, method, headers_json, body, kind, render, priority,
                    depth, parent_url, meta_json, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                values,
            )
            inserted = cursor.rowcount > 0
            if force and not inserted:
                # S2.5.42：force 重入队不再重置 attempts（保留重试计数），
                # 仅把状态拉回 pending 并清错误
                self.conn.execute(
                    "UPDATE frontier SET status='pending', last_error=NULL, priority=?, updated_at=? WHERE fingerprint=?",
                    (request.priority, now, request.fingerprint),
                )
            return inserted

    def claim(self, limit: int, strategy: str = "bfs") -> list[CrawlRequest]:
        order = self._CLAIM_ORDER.get(strategy)
        if order is None:
            order = self._CLAIM_ORDER["bfs"]
        claimed: list[sqlite3.Row] = []
        with self._lock, self.conn:
            # S2.5.3：候选先 SELECT 排序，再用条件 UPDATE（WHERE status='pending'）原子认领；
            # 被并发进程抢走的行 UPDATE 影响 0 行，跳过重取，杜绝 SELECT→UPDATE 双重认领。
            while len(claimed) < limit:
                rows = self.conn.execute(
                    f"SELECT * FROM frontier WHERE status='pending' ORDER BY {order} LIMIT ?",
                    (limit - len(claimed),),
                ).fetchall()
                if not rows:
                    break
                for row in rows:
                    cursor = self.conn.execute(
                        "UPDATE frontier SET status='in_progress', attempts=attempts+1, updated_at=? "
                        "WHERE fingerprint=? AND status='pending'",
                        (utcnow(), row["fingerprint"]),
                    )
                    if cursor.rowcount == 1:
                        claimed.append(row)
                        if len(claimed) >= limit:
                            break
        return [self._row_to_request(row) for row in claimed]

    @staticmethod
    def _row_to_request(row: sqlite3.Row) -> CrawlRequest:
        return CrawlRequest(
            url=row["url"], method=row["method"], headers=json.loads(row["headers_json"]),
            body=row["body"], kind=row["kind"], render=bool(row["render"]),
            priority=float(row["priority"]), depth=int(row["depth"]),
            parent_url=row["parent_url"], meta=json.loads(row["meta_json"]),
        )

    def mark_done(self, fingerprint: str, *, status: str = "done", error: str | None = None) -> None:
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE frontier SET status=?, last_error=?, updated_at=? WHERE fingerprint=?",
                (status, error, utcnow(), fingerprint),
            )

    def mark_failed(self, request: CrawlRequest, exc: Exception, max_attempts: int, retryable: bool = True) -> None:
        with self._lock, self.conn:
            row = self.conn.execute("SELECT attempts FROM frontier WHERE fingerprint=?", (request.fingerprint,)).fetchone()
            retry = bool(row and int(row["attempts"]) < max_attempts and retryable)
            self.conn.execute(
                "UPDATE frontier SET status=?, last_error=?, updated_at=? WHERE fingerprint=?",
                ("pending" if retry else "failed", str(exc)[:4000], utcnow(), request.fingerprint),
            )

    def pending_count(self) -> int:
        """S2.5.37：轻量单表 COUNT（走 idx_frontier_status 索引），
        替代 stats() 的五表全量聚合——高频循环内不再全表扫描。"""
        with self._lock:
            row = self.conn.execute(
                "SELECT COUNT(*) AS n FROM frontier WHERE status='pending'"
            ).fetchone()
            return int(row["n"])
