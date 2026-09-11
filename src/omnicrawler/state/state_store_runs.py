"""StateStore 的「运行生命周期与导出」域 —— 从 state_store.py 抽出的 Mixin（P1-3 第三批）。

含两族语义相近的方法：
- run 生命周期：``start_run`` / ``finish_run`` / ``transition_run`` /
  ``recover_incomplete_runs`` / ``latest_run`` / ``list_runs`` / ``run_events`` / ``run_stages``
- 检查点与导出：``save_checkpoint`` / ``checkpoint`` / ``begin_export`` /
  ``finish_export`` / ``fail_export`` / ``export_commit``

宿主共享方法 ``_require_run_id`` 经 TYPE_CHECKING 声明。
"""
from __future__ import annotations

import json
import uuid
from typing import TYPE_CHECKING, Any

from ..core.run_state import (
    TERMINAL_RUN_STATES,
    canonical_run_state,
    require_transition,
)
from ..core.utils import json_text, utcnow

if TYPE_CHECKING:
    import sqlite3


class RunsMixin:
    """run 生命周期 + 检查点/导出域。"""

    # ---- 宿主契约 ----
    conn: sqlite3.Connection
    _lock: Any

    if TYPE_CHECKING:
        @staticmethod
        def _require_run_id(run_id: str | None) -> str | None: ...
    def start_run(self, project_name: str, config_path: str) -> str:
        run_id = uuid.uuid4().hex
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO runs(run_id, project_name, config_path, started_at, status) VALUES(?,?,?,?,?)",
                (run_id, project_name, config_path, utcnow(), "pending"),
            )
            self.conn.execute(
                "INSERT INTO run_state_events(run_id, from_state, to_state, reason, details_json, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (run_id, "pending", "running", "start", "{}", utcnow()),
            )
            self.conn.execute("UPDATE runs SET status='running' WHERE run_id=?", (run_id,))
        return run_id

    def finish_run(self, run_id: str, status: str, summary: dict[str, Any]) -> None:
        self._require_run_id(run_id)
        target = canonical_run_state(status)
        if target not in TERMINAL_RUN_STATES:
            raise ValueError(f"完成任务必须使用终态: {target}")
        with self._lock, self.conn:
            row = self.conn.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(f"运行不存在: {run_id}")
            current, target = require_transition(str(row["status"]), target)
            self.conn.execute(
                "UPDATE runs SET finished_at=?, status=?, summary_json=? WHERE run_id=?",
                (utcnow(), target, json_text(summary), run_id),
            )
            if current != target:
                self.conn.execute(
                    "INSERT INTO run_state_events(run_id, from_state, to_state, reason, details_json, created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (run_id, current, target, "finish", json_text(summary), utcnow()),
                )

    def transition_run(
        self,
        run_id: str,
        target: str,
        *,
        reason: str = "manual",
        details: dict[str, Any] | None = None,
    ) -> str:
        self._require_run_id(run_id)
        with self._lock, self.conn:
            row = self.conn.execute("SELECT status FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise KeyError(f"运行不存在: {run_id}")
            current, target = require_transition(str(row["status"]), target)
            if current == target:
                return target
            self.conn.execute("UPDATE runs SET status=? WHERE run_id=?", (target, run_id))
            self.conn.execute(
                "INSERT INTO run_state_events(run_id, from_state, to_state, reason, details_json, created_at) "
                "VALUES(?,?,?,?,?,?)",
                (run_id, current, target, reason, json_text(details or {}), utcnow()),
            )
            return target

    def recover_incomplete_runs(self, *, stale_seconds: float = 0.0) -> list[str]:
        """Move crash-interrupted runs to retrying without claiming they succeeded.

        FINAL-D3：提供可选的陈旧阈值门控 ``stale_seconds``——仅当多进程共享
        同一 state 库、且需保护存活进程的 in_progress 行时才显式传入（如 3600）。
        默认 0 = 无差别重置，保持恢复中心"崩溃后立即抢救"的原始语义
        （桌面单人场景的恢复动作总是紧跟崩溃发生，阈值反而会挡住正主）。
        """
        from datetime import UTC, datetime, timedelta

        recovered: list[str] = []
        with self._lock, self.conn:
            rows = self.conn.execute(
                "SELECT run_id, status FROM runs WHERE status IN ('running','paused','retrying')"
            ).fetchall()
            for row in rows:
                current = str(row["status"])
                if current == "paused":
                    self.conn.execute("UPDATE runs SET status='running' WHERE run_id=?", (row["run_id"],))
                    self.conn.execute(
                        "INSERT INTO run_state_events(run_id, from_state, to_state, reason, details_json, created_at) "
                        "VALUES(?,?,?,?,?,?)",
                        (row["run_id"], "paused", "running", "crash_recovery", "{}", utcnow()),
                    )
                    current = "running"
                if current == "running":
                    target = "retrying"
                    self.conn.execute("UPDATE runs SET status=? WHERE run_id=?", (target, row["run_id"]))
                    self.conn.execute(
                        "INSERT INTO run_state_events(run_id, from_state, to_state, reason, details_json, created_at) "
                        "VALUES(?,?,?,?,?,?)",
                        (row["run_id"], current, target, "crash_recovery", "{}", utcnow()),
                    )
                recovered.append(str(row["run_id"]))
                self.conn.execute(
                    "UPDATE export_commits SET status='retrying', updated_at=? "
                    "WHERE run_id=? AND status='running'",
                    (utcnow(), row["run_id"]),
                )
            stale_cutoff = (
                datetime.now(UTC) - timedelta(seconds=max(0.0, float(stale_seconds)))
            ).isoformat(timespec="microseconds")
            self.conn.execute(
                "UPDATE frontier SET status='pending', updated_at=? "
                "WHERE status='in_progress' AND updated_at<=?",
                (utcnow(), stale_cutoff),
            )
        return recovered

    def save_checkpoint(
        self,
        run_id: str,
        stage: str,
        idempotency_key: str,
        payload: dict[str, Any],
        *,
        status: str = "succeeded",
    ) -> None:
        self._require_run_id(run_id)
        if not stage.strip() or not idempotency_key.strip():
            raise ValueError("stage和idempotency_key不能为空")
        with self._lock, self.conn:
            self.conn.execute(
                """
                INSERT INTO stage_checkpoints(run_id, stage, idempotency_key, status, payload_json, updated_at)
                VALUES(?,?,?,?,?,?)
                ON CONFLICT(run_id, stage, idempotency_key) DO UPDATE SET
                    status=excluded.status, payload_json=excluded.payload_json, updated_at=excluded.updated_at
                """,
                (run_id, stage, idempotency_key, status, json_text(payload), utcnow()),
            )

    def checkpoint(self, run_id: str, stage: str, idempotency_key: str) -> dict[str, Any] | None:
        self._require_run_id(run_id)
        with self._lock:
            row = self.conn.execute(
                "SELECT status, payload_json, updated_at FROM stage_checkpoints "
                "WHERE run_id=? AND stage=? AND idempotency_key=?",
                (run_id, stage, idempotency_key),
            ).fetchone()
        if row is None:
            return None
        return {
            "status": row["status"],
            "payload": json.loads(row["payload_json"]),
            "updated_at": row["updated_at"],
        }

    def begin_export(
        self, run_id: str, exporter: str, idempotency_key: str, *, force: bool = False,
    ) -> bool:
        self._require_run_id(run_id)
        with self._lock, self.conn:
            cursor = self.conn.execute(
                "INSERT OR IGNORE INTO export_commits(idempotency_key, run_id, exporter, status, result_json, updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (idempotency_key, run_id, exporter, "running", "{}", utcnow()),
            )
            if cursor.rowcount > 0:
                return True
            if force:
                # S2.5.2：reprocess 强制刷新——已成功的提交也降回 running 重新导出
                cursor = self.conn.execute(
                    "UPDATE export_commits SET status='running', updated_at=? "
                    "WHERE idempotency_key=? AND run_id=? AND exporter=? "
                    "AND status IN ('failed','retrying','succeeded')",
                    (utcnow(), idempotency_key, run_id, exporter),
                )
            else:
                cursor = self.conn.execute(
                    "UPDATE export_commits SET status='running', updated_at=? "
                    "WHERE idempotency_key=? AND run_id=? AND exporter=? AND status IN ('failed','retrying')",
                    (utcnow(), idempotency_key, run_id, exporter),
                )
            return cursor.rowcount > 0

    def finish_export(self, idempotency_key: str, result: dict[str, Any]) -> None:
        with self._lock, self.conn:
            cursor = self.conn.execute(
                "UPDATE export_commits SET status='succeeded', result_json=?, updated_at=? "
                "WHERE idempotency_key=? AND status='running'",
                (json_text(result), utcnow(), idempotency_key),
            )
            if cursor.rowcount != 1:
                raise ValueError("导出提交不存在、已完成或状态无效")

    def fail_export(self, idempotency_key: str, error: str) -> None:
        with self._lock, self.conn:
            cursor = self.conn.execute(
                "UPDATE export_commits SET status='failed', result_json=?, updated_at=? "
                "WHERE idempotency_key=? AND status='running'",
                (json_text({"error": error[:4000]}), utcnow(), idempotency_key),
            )
            if cursor.rowcount != 1:
                raise ValueError("导出提交不存在或状态无效")

    def export_commit(self, idempotency_key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute(
                "SELECT run_id, exporter, status, result_json, updated_at FROM export_commits "
                "WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
        if row is None:
            return None
        return {
            "run_id": row["run_id"],
            "exporter": row["exporter"],
            "status": row["status"],
            "result": json.loads(row["result_json"]),
            "updated_at": row["updated_at"],
        }

    def latest_run(self) -> dict[str, Any] | None:
        with self._lock:
            row = self.conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def list_runs(self, limit: int = 30) -> list[dict[str, Any]]:
        """最近运行列表（按开始时间倒序），供只读检视/时间线选择。"""
        limit = max(1, int(limit))
        with self._lock:
            rows = self.conn.execute(
                "SELECT run_id, project_name, status, started_at, finished_at "
                "FROM runs ORDER BY started_at DESC, rowid DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def run_events(self, run_id: str) -> list[dict[str, Any]]:
        """指定运行的状态迁移事件（时间线），按发生时间升序。"""
        with self._lock:
            rows = self.conn.execute(
                "SELECT from_state, to_state, reason, details_json, created_at "
                "FROM run_state_events WHERE run_id=? ORDER BY created_at ASC, rowid ASC",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def run_stages(self, run_id: str) -> list[dict[str, Any]]:
        """指定运行的阶段 checkpoint（stage 事件），按更新时间升序。"""
        with self._lock:
            rows = self.conn.execute(
                "SELECT stage, idempotency_key, status, updated_at "
                "FROM stage_checkpoints WHERE run_id=? ORDER BY updated_at ASC, rowid ASC",
                (run_id,),
            ).fetchall()
        return [dict(row) for row in rows]
