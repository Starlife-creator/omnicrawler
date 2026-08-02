from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .utils import utcnow

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS documents (
    doc_id TEXT PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    primary_path TEXT NOT NULL,
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    page_count INTEGER,
    is_encrypted INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'ingested',
    document_type TEXT,
    text_page_count INTEGER NOT NULL DEFAULT 0,
    ocr_page_count INTEGER NOT NULL DEFAULT 0,
    candidate_page_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    parser_version TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS document_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    source_path TEXT NOT NULL UNIQUE,
    source_url TEXT,
    source_meta_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pages (
    doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    page_no INTEGER NOT NULL,
    width REAL,
    height REAL,
    native_text TEXT,
    ocr_text TEXT,
    final_text TEXT,
    parse_method TEXT,
    printable_chars INTEGER NOT NULL DEFAULT 0,
    garbled_ratio REAL NOT NULL DEFAULT 0,
    needs_ocr INTEGER NOT NULL DEFAULT 0,
    ocr_status TEXT NOT NULL DEFAULT 'not_needed',
    ocr_confidence REAL,
    candidate_score REAL NOT NULL DEFAULT 0,
    is_candidate INTEGER NOT NULL DEFAULT 0,
    evidence_json TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (doc_id, page_no)
);

CREATE TABLE IF NOT EXISTS records (
    record_id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    record_index INTEGER NOT NULL,
    extraction_method TEXT NOT NULL,
    confidence REAL,
    review_status TEXT NOT NULL DEFAULT 'unreviewed',
    validation_status TEXT NOT NULL DEFAULT 'pending',
    validation_messages TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(doc_id, record_index)
);

CREATE TABLE IF NOT EXISTS field_values (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_id TEXT NOT NULL REFERENCES records(record_id) ON DELETE CASCADE,
    field_name TEXT NOT NULL,
    raw_value TEXT,
    normalized_value TEXT,
    unit TEXT,
    page_no INTEGER,
    evidence TEXT,
    extraction_method TEXT,
    confidence REAL,
    validation_status TEXT NOT NULL DEFAULT 'pending',
    UNIQUE(record_id, field_name)
);

CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT,
    stage TEXT NOT NULL,
    error_type TEXT NOT NULL,
    message TEXT NOT NULL,
    retryable INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    command TEXT NOT NULL,
    config_path TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    summary_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_pages_ocr ON pages(needs_ocr, ocr_status);
CREATE INDEX IF NOT EXISTS idx_pages_candidate ON pages(doc_id, is_candidate);
CREATE INDEX IF NOT EXISTS idx_records_doc ON records(doc_id);
CREATE INDEX IF NOT EXISTS idx_field_values_name ON field_values(field_name);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=60)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=60000")
        self.connection.executescript(SCHEMA)
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(document_sources)")
        }
        if "source_meta_json" not in columns:
            self.connection.execute(
                "ALTER TABLE document_sources ADD COLUMN source_meta_json TEXT"
            )
            self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self.connection
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def fetchall(self, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        return list(self.connection.execute(sql, params).fetchall())

    def fetchone(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        return self.connection.execute(sql, params).fetchone()

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        cursor = self.connection.execute(sql, params)
        self.connection.commit()
        return cursor

    def add_error(self, doc_id: str | None, stage: str, exc: Exception, retryable: bool = True) -> None:
        self.execute(
            "INSERT INTO errors(doc_id, stage, error_type, message, retryable, created_at) VALUES(?,?,?,?,?,?)",
            (doc_id, stage, type(exc).__name__, str(exc)[:4000], int(retryable), utcnow()),
        )

    def reset_stage(self, stage: str) -> None:
        now = utcnow()
        with self.transaction() as conn:
            if stage == "parse":
                conn.execute("DELETE FROM records")
                conn.execute("DELETE FROM pages")
                conn.execute(
                    """UPDATE documents SET status='ingested', error=NULL,
                           document_type=NULL, text_page_count=0, ocr_page_count=0,
                           candidate_page_count=0, parser_version=NULL, updated_at=?
                       WHERE status NOT IN ('invalid','needs_password')""",
                    (now,),
                )
            elif stage == "ocr":
                conn.execute("DELETE FROM records")
                conn.execute(
                    """UPDATE pages SET ocr_text=NULL, final_text=native_text,
                           parse_method='native',
                           ocr_status=CASE WHEN needs_ocr=1 THEN 'pending' ELSE 'not_needed' END,
                           ocr_confidence=NULL, candidate_score=0, is_candidate=0,
                           evidence_json=NULL, updated_at=?""",
                    (now,),
                )
                conn.execute(
                    """UPDATE documents SET
                           status=CASE
                               WHEN EXISTS(
                                   SELECT 1 FROM pages p
                                   WHERE p.doc_id=documents.doc_id AND p.needs_ocr=1
                               ) THEN 'parsed_native'
                               ELSE 'parsed'
                           END,
                           document_type=NULL, ocr_page_count=0,
                           candidate_page_count=0, updated_at=?
                       WHERE EXISTS(
                           SELECT 1 FROM pages p WHERE p.doc_id=documents.doc_id
                       )""",
                    (now,),
                )
            elif stage == "extract":
                conn.execute("DELETE FROM records")
                conn.execute(
                    """UPDATE pages SET candidate_score=0, is_candidate=0,
                           evidence_json=NULL, updated_at=?""",
                    (now,),
                )
                conn.execute(
                    """UPDATE documents SET
                           status=CASE
                               WHEN EXISTS(
                                   SELECT 1 FROM pages p
                                   WHERE p.doc_id=documents.doc_id
                                     AND p.needs_ocr=1 AND p.ocr_status!='done'
                               ) THEN CASE
                                   WHEN EXISTS(
                                       SELECT 1 FROM pages p
                                       WHERE p.doc_id=documents.doc_id
                                         AND p.ocr_status='done'
                                   ) THEN 'parsed_partial'
                                   ELSE 'parsed_native'
                               END
                               ELSE 'parsed'
                           END,
                           document_type=NULL, candidate_page_count=0, updated_at=?
                       WHERE EXISTS(
                           SELECT 1 FROM pages p WHERE p.doc_id=documents.doc_id
                       )""",
                    (now,),
                )
            else:
                raise ValueError("stage 必须是 parse、ocr 或 extract")
