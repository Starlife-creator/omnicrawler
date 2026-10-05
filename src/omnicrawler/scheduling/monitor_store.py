"""Atomic monitor observations and recoverable delivery attempts."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class MonitorStore:
    def __init__(self, directory: Path) -> None:
        self.path = directory / "monitor.sqlite3"

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS baselines(rule_id TEXT PRIMARY KEY, body_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS deliveries(
                    event_id TEXT PRIMARY KEY, rule_id TEXT NOT NULL, body_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0, lease_until REAL NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '');
            """)
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
             *, pending: bool = True) -> None:
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("INSERT INTO baselines VALUES(?,?) ON CONFLICT(rule_id) DO UPDATE SET body_json=excluded.body_json",
                               (rule_id, json.dumps(baseline, ensure_ascii=False)))
            if event is not None:
                connection.execute("INSERT OR IGNORE INTO deliveries(event_id,rule_id,body_json,status) VALUES(?,?,?,?)",
                                   (event["event_id"], rule_id, json.dumps(event, ensure_ascii=False), "pending" if pending else "submitted"))

    def pending(self, rule_ids: set[str], *, force: bool = False) -> list[dict[str, Any]]:
        if not self.path.is_file() or not rule_ids:
            return []
        now = time.time()
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM deliveries WHERE status IN ('pending','retrying','sending','failed') ORDER BY rowid").fetchall()
        return [dict(row) for row in rows if row["rule_id"] in rule_ids
                and row["lease_until"] <= now
                and (force or row["status"] != "failed" and row["next_attempt"] <= now)]

    def claim(self, event_id: str) -> bool:
        with self.connection() as connection:
            now = time.time()
            cursor = connection.execute("UPDATE deliveries SET status='sending',lease_until=?,attempts=attempts+1 "
                                        "WHERE event_id=? AND status<>'submitted' AND lease_until<=?",
                                        (now + 120, event_id, now))
            return cursor.rowcount == 1

    def acknowledge(self, event_id: str) -> None:
        with self.connection() as connection:
            connection.execute("UPDATE deliveries SET status='submitted',lease_until=0,error='' WHERE event_id=?", (event_id,))

    def fail(self, event_id: str) -> None:
        with self.connection() as connection:
            row = connection.execute("SELECT attempts FROM deliveries WHERE event_id=?", (event_id,)).fetchone()
            if row is not None:
                attempts = int(row["attempts"])
                connection.execute("UPDATE deliveries SET status=?,lease_until=0,next_attempt=?,error=? WHERE event_id=?",
                                   ("failed" if attempts >= 8 else "retrying", time.time() + min(300, 2 ** attempts),
                                    "Notification callback failed; inspect local logs", event_id))

    def report(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        with self.connection() as connection:
            return [dict(row) for row in connection.execute(
                "SELECT event_id,rule_id,status,attempts,next_attempt,error FROM deliveries ORDER BY rowid DESC LIMIT 100")]
