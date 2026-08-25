from __future__ import annotations

import builtins
import json
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.safe_data import safe_json_loads

SCHEMA = """
CREATE TABLE IF NOT EXISTS schedules (
    schedule_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    config_path TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    next_run_at REAL NOT NULL,
    lease_until REAL,
    last_started_at REAL,
    last_finished_at REAL,
    last_status TEXT,
    last_error TEXT,
    conditions_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_schedules_due ON schedules(enabled, next_run_at, lease_until);
"""


class ScheduleStore:
    """Lease-based local scheduler; safe for multiple cron/Task Scheduler processes."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.executescript(SCHEMA)
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(schedules)")}
        if "conditions_json" not in columns:
            with self.conn:
                self.conn.execute("ALTER TABLE schedules ADD COLUMN conditions_json TEXT NOT NULL DEFAULT '{}'")
        # S2.5.44：并行 run_due 时共享连接的进程内互斥
        self._lock = threading.Lock()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> ScheduleStore:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def add(
        self,
        name: str,
        config_path: Path,
        interval_seconds: int,
        *,
        start_at: float | None = None,
        conditions: dict[str, Any] | None = None,
    ) -> str:
        if interval_seconds < 60:
            raise ValueError("Scheduled interval must be at least 60 seconds")
        config_path = config_path.expanduser().resolve()
        if not config_path.is_file():
            raise FileNotFoundError(config_path)
        schedule_id = uuid.uuid4().hex
        with self.conn:
            self.conn.execute(
                "INSERT INTO schedules(schedule_id, name, config_path, interval_seconds, next_run_at, conditions_json) VALUES(?,?,?,?,?,?)",
                (
                    schedule_id,
                    name.strip() or config_path.stem,
                    str(config_path),
                    interval_seconds,
                    start_at or time.time(),
                    json.dumps(conditions or {}, ensure_ascii=False),
                ),
            )
        return schedule_id

    def list(self) -> builtins.list[dict[str, Any]]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM schedules ORDER BY name, schedule_id")]

    def set_enabled(self, schedule_id: str, enabled: bool) -> None:
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE schedules SET enabled=?, lease_until=NULL WHERE schedule_id=?",
                (int(enabled), schedule_id),
            )
            if cursor.rowcount == 0:
                raise KeyError(schedule_id)

    def claim_due(self, *, now: float | None = None, lease_seconds: int = 300, limit: int = 10) -> builtins.list[dict[str, Any]]:
        now = time.time() if now is None else now
        with self._lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self.conn.execute(
                    """
                    SELECT * FROM schedules
                    WHERE enabled=1 AND next_run_at<=? AND (lease_until IS NULL OR lease_until<=?)
                    ORDER BY next_run_at LIMIT ?
                    """,
                    (now, now, limit),
                ).fetchall()
                if rows:
                    self.conn.executemany(
                        "UPDATE schedules SET lease_until=?, last_started_at=?, last_status='running' WHERE schedule_id=?",
                        [(now + lease_seconds, now, row["schedule_id"]) for row in rows],
                    )
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise
        return [dict(row) for row in rows]

    def finish(self, schedule_id: str, *, ok: bool, error: str = "", now: float | None = None) -> None:
        now = time.time() if now is None else now
        # FINAL-R1：必须显式事务提交——run-due 为一次性进程，裸 UPDATE 在进程
        # 退出时被回滚，导致 next_run_at 不推进、任务在租约过期后反复重复执行。
        with self._lock, self.conn:
            row = self.conn.execute(
                "SELECT interval_seconds FROM schedules WHERE schedule_id=?",
                (schedule_id,),
            ).fetchone()
            if row is None:
                # S2.5.19：调度已被删除时静默兜底，不再中断整批循环
                return
            self.conn.execute(
                """
                UPDATE schedules SET lease_until=NULL, last_finished_at=?, last_status=?, last_error=?, next_run_at=?
                WHERE schedule_id=?
                """,
                (now, "succeeded" if ok else "failed", error[:4000], now + int(row["interval_seconds"]), schedule_id),
            )

    def defer(self, schedule_id: str, reason: str, *, seconds: int = 300) -> None:
        # FINAL-R1：同 finish——defer 结果也必须落盘。
        with self._lock, self.conn:
            self.conn.execute(
                "UPDATE schedules SET lease_until=NULL, last_status='waiting_condition', last_error=?, next_run_at=? WHERE schedule_id=?",
                (reason[:4000], time.time() + max(60, seconds), schedule_id),
            )

    def run_due(self, executor: Callable[[Path], Any], *, limit: int = 10) -> builtins.list[dict[str, Any]]:
        # S2.5.44：并行执行到期任务——长任务不再拖住后续调度
        from concurrent.futures import ThreadPoolExecutor

        schedules = self.claim_due(limit=limit)
        if not schedules:
            return []
        workers = min(len(schedules), 8)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(self._run_one, schedule, executor) for schedule in schedules
            ]
            return [future.result() for future in futures]

    def _run_one(
        self, schedule: dict[str, Any], executor: Callable[[Path], Any],
    ) -> dict[str, Any]:
        from .schedule_conditions import evaluate_conditions

        conditions = safe_json_loads(schedule.get("conditions_json") or "{}", default={})
        allowed, reason = evaluate_conditions(conditions if isinstance(conditions, dict) else {})
        if not allowed:
            self.defer(schedule["schedule_id"], reason)
            return {
                "schedule_id": schedule["schedule_id"], "ok": True, "deferred": True, "reason": reason,
            }
        try:
            value = executor(Path(schedule["config_path"]))
        except Exception as exc:
            self.finish(schedule["schedule_id"], ok=False, error=f"{type(exc).__name__}: {exc}")
            return {"schedule_id": schedule["schedule_id"], "ok": False, "error": str(exc)}
        self.finish(schedule["schedule_id"], ok=True)
        return {"schedule_id": schedule["schedule_id"], "ok": True, "result": value}
