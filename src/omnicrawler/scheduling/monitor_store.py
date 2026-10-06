"""Atomic monitor observations and recoverable delivery attempts."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

_UNSET = object()


class BaselineConflictError(RuntimeError):
    """Another owner changed or removed the expected monitor baseline."""


class MonitorStore:
    def __init__(self, directory: Path) -> None:
        self.path = directory / "monitor.sqlite3"
        self._claims: dict[tuple[str, str], str] = {}

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS baselines(rule_id TEXT PRIMARY KEY, body_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observations(
                    id INTEGER PRIMARY KEY,rule_id TEXT NOT NULL,body_json TEXT NOT NULL,observed_at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS target_deliveries(
                    event_id TEXT NOT NULL,target_id TEXT NOT NULL,rule_id TEXT NOT NULL,body_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0,lease_until REAL NOT NULL DEFAULT 0,error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(event_id,target_id));
                CREATE TABLE IF NOT EXISTS deliveries(
                    event_id TEXT PRIMARY KEY, rule_id TEXT NOT NULL, body_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '');
            """)
            connection.execute("BEGIN IMMEDIATE")
            for table in ("deliveries", "target_deliveries"):
                columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
                if "lease_token" not in columns:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN lease_token TEXT NOT NULL DEFAULT ''")
            connection.commit()
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def baselines(self) -> dict[str, dict[str, Any]]:
        if not self.path.is_file():
            return {}
        with self.connection() as connection:
            return {row["rule_id"]: json.loads(row["body_json"]) for row in connection.execute("SELECT * FROM baselines")}

    def save(self, rule_id: str, baseline: dict[str, Any], event: dict[str, Any] | None = None,
             *, pending: bool = True, targets: list[str] | None = None, expected: Any = _UNSET) -> None:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute("SELECT body_json FROM baselines WHERE rule_id=?", (rule_id,)).fetchone()
            current = json.loads(previous[0]) if previous is not None else None
            if expected is not _UNSET and current != expected:
                raise BaselineConflictError("监控基线已被其它执行更新，请重新检查")
            if "observed_content" in baseline:
                connection.execute("INSERT INTO observations(rule_id,body_json,observed_at) VALUES(?,?,?)",
                                   (rule_id, json.dumps({"content": baseline["observed_content"],
                                                        "hash": baseline.get("observed_hash")}, ensure_ascii=False), time.time()))
            connection.execute("INSERT INTO baselines VALUES(?,?) ON CONFLICT(rule_id) DO UPDATE SET body_json=excluded.body_json",
                               (rule_id, json.dumps(baseline, ensure_ascii=False)))
            if event is not None:
                connection.execute("INSERT OR IGNORE INTO deliveries(event_id,rule_id,body_json,status) VALUES(?,?,?,?)",
                                   (event["event_id"], rule_id, json.dumps(event, ensure_ascii=False), "suppressed" if not event.get("notification_eligible", True) else "pending" if pending else "submitted"))
                for target in targets or []:
                    connection.execute("INSERT OR IGNORE INTO target_deliveries(event_id,target_id,rule_id,body_json,status) VALUES(?,?,?,?,?)",
                                       (event["event_id"], target, rule_id, json.dumps(event, ensure_ascii=False),
                                        "pending" if event.get("notification_eligible", True) else "suppressed"))

    def pending(self, rule_ids: set[str], *, force: bool = False, target: bool = False) -> list[dict[str, Any]]:
        if not self.path.is_file() or not rule_ids:
            return []
        now = time.time()
        with self.connection() as connection:
            table = "target_deliveries" if target else "deliveries"
            rows = connection.execute(
                f"SELECT * FROM {table} WHERE rule_id IN (SELECT value FROM json_each(?)) "
                "AND status IN ('pending','retrying','sending','failed') AND lease_until<=? "
                "AND (? OR (status!='failed' AND next_attempt<=?)) ORDER BY rowid LIMIT 1000",
                (json.dumps(sorted(rule_ids)), now, force, now)).fetchall()
        return [dict(row) for row in rows]

    def claim(self, event_id: str, *, target_id: str = "") -> str:
        with self.connection() as connection:
            now = time.time()
            table = "target_deliveries" if target_id else "deliveries"
            suffix, params = (" AND target_id=?", [target_id]) if target_id else ("", [])
            token = uuid4().hex
            cursor = connection.execute(f"UPDATE {table} SET status='sending',lease_until=?,lease_token=?,attempts=attempts+1 "
                                        "WHERE event_id=? AND status IN ('pending','retrying','failed','sending') AND lease_until<=?" + suffix,
                                        [now + 120, token, event_id, now, *params])
            if cursor.rowcount == 1:
                self._claims[(event_id, target_id)] = token
                return token
            return ""

    def acknowledge(self, event_id: str, *, target_id: str = "", lease_token: str = "") -> None:
        with self.connection() as connection:
            table = "target_deliveries" if target_id else "deliveries"
            suffix, params = (" AND target_id=?", [target_id]) if target_id else ("", [])
            token = lease_token or self._claims.get((event_id, target_id), "")
            guard = " AND status='sending' AND lease_token=?" if token else " AND status IN ('pending','retrying','failed') AND lease_until<=?"
            connection.execute(f"UPDATE {table} SET status='submitted',lease_until=0,error='' WHERE event_id=?" + suffix + guard,
                               [event_id, *params, token or time.time()])
        if self._claims.get((event_id, target_id)) == token:
            self._claims.pop((event_id, target_id), None)

    def fail(self, event_id: str, *, target_id: str = "", error: str = "notification_failed", delay: float | None = None, permanent: bool = False, lease_token: str = "") -> None:
        with self.connection() as connection:
            table = "target_deliveries" if target_id else "deliveries"
            suffix, params = (" AND target_id=?", [target_id]) if target_id else ("", [])
            token = lease_token or self._claims.get((event_id, target_id), "")
            guard = " AND status='sending' AND lease_token=?" if token else " AND status IN ('pending','retrying','failed') AND lease_until<=?"
            guarded = [event_id, *params, token or time.time()]
            row = connection.execute(f"SELECT attempts FROM {table} WHERE event_id=?" + suffix + guard, guarded).fetchone()
            if row is not None:
                attempts = int(row["attempts"])
                connection.execute(f"UPDATE {table} SET status=?,lease_until=0,next_attempt=?,error=? WHERE event_id=?" + suffix + guard,
                                   ["failed" if attempts >= 8 or permanent else "retrying",
                                    time.time() + min(300, max(0, delay if delay is not None else 2 ** attempts)),
                                    error, *guarded])
        if self._claims.get((event_id, target_id)) == token:
            self._claims.pop((event_id, target_id), None)

    def revoke_targets(self, active: dict[str, set[str]]) -> None:
        with self.connection() as connection:
            rows = connection.execute("SELECT event_id,target_id,rule_id FROM target_deliveries WHERE status IN ('pending','retrying','sending','failed')").fetchall()
            for row in rows:
                if row["target_id"] not in active.get(row["rule_id"], set()):
                    connection.execute("UPDATE target_deliveries SET status='cancelled',lease_until=0 WHERE event_id=? AND target_id=?",
                                       (row["event_id"], row["target_id"]))

    def observation_report(self, rule_id: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return [json.loads(row[0]) for row in connection.execute(
                "SELECT body_json FROM observations WHERE rule_id=? ORDER BY id DESC LIMIT 100", (rule_id,))]

    def report(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        with self.connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT event_id,rule_id,status,attempts,next_attempt,error,'desktop' AS target_id FROM deliveries "
                "UNION ALL SELECT event_id,rule_id,status,attempts,next_attempt,error,target_id FROM target_deliveries "
                "ORDER BY next_attempt DESC LIMIT 200")]
