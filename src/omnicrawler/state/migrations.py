"""Transactional SQLite upgrades with a consistent pre-upgrade recovery copy."""
from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from uuid import uuid4

SCHEMA_VERSION = 2


def _backup(conn: sqlite3.Connection, path: Path, version: int) -> Path | None:
    if str(path) == ":memory:" or not conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' LIMIT 1"
    ).fetchone():
        return None
    target = path.with_name(f".{path.name}.pre-v{version}-{uuid4().hex}.sqlite")
    temporary = target.with_suffix(".tmp")
    deadline = time.monotonic() + 30

    def progress(_status: int, _remaining: int, _total: int) -> None:
        if time.monotonic() >= deadline:
            raise TimeoutError("状态库升级前备份超时；尚未修改数据库")

    try:
        # A separate reader includes committed WAL while our reserved writer
        # lock prevents another process from changing the migration snapshot.
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(temporary)) as destination:
                source.backup(destination, pages=256, progress=progress, sleep=0.05)
                if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("状态库升级前备份完整性检查失败")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def initialize_schema(conn: sqlite3.Connection, path: Path, schema: str) -> None:
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise RuntimeError(f"状态库版本 {version} 高于当前支持的 {SCHEMA_VERSION}；请升级程序，未修改数据库")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("BEGIN IMMEDIATE")
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("状态库已由更新版本升级；请升级程序")
        if version == SCHEMA_VERSION:
            conn.commit()
            return
        backup = _backup(conn, path, version)
        statement = ""
        for line in schema.splitlines(keepends=True):
            if line.strip().upper().startswith("PRAGMA "):
                continue
            statement += line
            if sqlite3.complete_statement(statement):
                conn.execute(statement)
                statement = ""
        if statement.strip():
            raise ValueError("状态库迁移 SQL 不完整")
        for table, columns in {
            "responses": {"etag": "TEXT", "last_modified": "TEXT"},
            "semantic_changes": {"baseline": "INTEGER NOT NULL DEFAULT 0"},
        }.items():
            present = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for name, declaration in columns.items():
                if name not in present:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, backup_name TEXT)")
        conn.execute("INSERT INTO schema_migrations VALUES (?, datetime('now'), ?)",
                     (SCHEMA_VERSION, backup.name if backup else None))
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
