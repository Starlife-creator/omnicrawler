"""DuckDB 单文件分析库分批读写与动态模式演进后端（可选依赖 duckdb）。"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from ._common import (
    _PROGRESS_CHUNK,
    _SQL_IDENTIFIER_RE,
    READERS,
    WRITERS,
    CanonicalRecords,
    ReaderFn,
    WriterFn,
    _ensure_parent_dir,
    _ordered_columns,
    _ProgressEmitter,
    _require_file,
    atomic_output,
    check_cancel,
    register_reader,
    register_writer,
)

# ── DuckDB ────────────────────────────────────────────────
_VALID_COLUMN_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


def _iter_duckdb(path: Path, options: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield DuckDB cursor batches while keeping one read-only connection."""
    import duckdb

    _require_file(path)
    check_cancel(options)
    if "_read_stats" in options:
        options["_read_stats"]["complete"] = True
    pe = _ProgressEmitter(options.get("on_line_progress"))
    table = str(options.get("table", "records"))
    if not _SQL_IDENTIFIER_RE.fullmatch(table):
        raise ValueError(f"无效的 duckdb 表名: {table!r}")
    total_rows = 0
    connection = duckdb.connect(str(path), read_only=True)
    try:
        try:
            count_row = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
            if count_row is not None:
                total_rows = int(count_row[0] or 0)
        except Exception:  # noqa: BLE001 - count is optional progress metadata
            total_rows = 0
        cursor = connection.execute(f"SELECT * FROM {table}")
        columns = [description[0] for description in cursor.description or []]
        accepted = 0
        while True:
            check_cancel(options)
            chunk = cursor.fetchmany(_PROGRESS_CHUNK)
            if not chunk:
                break
            for row in chunk:
                accepted += 1
                yield dict(zip(columns, row, strict=True))
            pe.emit(line_num=accepted, records_so_far=accepted, total_rows=total_rows)
        pe.flush(line_num=accepted, records_so_far=accepted, total_rows=total_rows)
    finally:
        connection.close()


def _duckdb_validate_columns(columns: Iterable[str]) -> list[str]:
    out: list[str] = []
    for column in columns:
        safe = re.sub(r"[^a-zA-Z0-9_]", "_", str(column))
        if not safe:
            safe = "col"
        if not safe[0].isalpha() and safe[0] != "_":
            safe = "c_" + safe
        out.append(safe)
    return out


def _duckdb_type_name(kinds: set[str]) -> str:
    if not kinds:
        return "VARCHAR"
    if kinds <= {"bool"}:
        return "BOOLEAN"
    if kinds <= {"int"}:
        return "BIGINT"
    if kinds <= {"float", "int"}:
        return "DOUBLE"
    return "VARCHAR"


def _duckdb_add_kind(kinds: set[str], value: Any) -> None:
    if value is None or value == "":
        return
    if isinstance(value, bool):
        kinds.add("bool")
    elif isinstance(value, int):
        kinds.add("int")
    elif isinstance(value, float):
        kinds.add("float")
    else:
        kinds.add("other")


def _plan_duckdb_schema(
    rows: Iterable[dict[str, Any]], options: dict[str, Any]
) -> tuple[list[str], list[str], list[str], int]:
    """Discover columns and scalar types without retaining the input records."""
    preferred = options.get("columns") or []
    seen: dict[str, None] = {}
    kinds: dict[str, set[str]] = {}
    total = 0
    for row in rows:
        check_cancel(options)
        total += 1
        for key, value in row.items():
            column = str(key)
            seen.setdefault(column, None)
            _duckdb_add_kind(kinds.setdefault(column, set()), value)
    safe_columns = _ordered_columns([], prefer=[*preferred, *seen])
    typed_columns = _duckdb_validate_columns(safe_columns)
    column_types = [_duckdb_type_name(kinds.get(column, set())) for column in safe_columns]
    return safe_columns, typed_columns, column_types, total


def _write_duckdb_batches(
    rows: Iterable[dict[str, Any]],
    path: Path,
    options: dict[str, Any],
    *,
    safe_columns: list[str],
    typed_columns: list[str],
    column_types: list[str],
    total: int,
) -> dict[str, Any]:
    """Insert planned rows in transactions while retaining only one batch."""
    import duckdb

    _ensure_parent_dir(path)
    check_cancel(options)
    pe = _ProgressEmitter(options.get("on_write_progress"))
    table = str(options.get("table", "records"))
    if not _SQL_IDENTIFIER_RE.fullmatch(table):
        raise ValueError(f"无效的 duckdb 表名: {table!r}")
    ddl = ", ".join(
        f'"{column}" {column_type}'
        for column, column_type in zip(typed_columns, column_types, strict=True)
    )
    placeholders = ", ".join("?" for _ in typed_columns)
    written = 0
    existing = path.exists()
    with (nullcontext(path) if existing else atomic_output(path, options)) as db_path:
        if not existing:
            # DuckDB creates its own header; mkstemp's empty file is not a database.
            db_path.unlink()
        con = duckdb.connect(str(db_path))
        try:
            con.execute("BEGIN TRANSACTION")
            con.execute(f"DROP TABLE IF EXISTS {table}")
            con.execute(f"CREATE TABLE {table} ({ddl})")
            batch: list[dict[str, Any]] = []
            for row in rows:
                check_cancel(options)
                batch.append(row)
                if len(batch) < _PROGRESS_CHUNK:
                    continue
                values = [[row.get(column) for column in safe_columns] for row in batch]
                con.executemany(f"INSERT INTO {table} VALUES ({placeholders})", values)
                written += len(batch)
                batch.clear()
                # B-P2-7 回退：原先按批 COMMIT 会让「取消 → 目标表回滚」契约失效
                # （实测取消后目标表残留 250 行半成品）。转换走单一事务，取消即整体回滚；
                # 长事务持锁的代价接受。
                pe.emit(written=written, total=total)
            if batch:
                values = [[row.get(column) for column in safe_columns] for row in batch]
                con.executemany(f"INSERT INTO {table} VALUES ({placeholders})", values)
                written += len(batch)
                batch.clear()
            pe.flush(written=written, total=total)
            check_cancel(options)
            con.execute("COMMIT")
        finally:
            con.close()
    return {"rows": written, "columns": safe_columns, "table": table}


def _register_duckdb() -> None:
    try:
        import duckdb  # noqa: F401
    except ImportError:
        return

    @register_reader(".duckdb", ".db")
    def read_duckdb(path: Path, options: dict[str, Any]) -> CanonicalRecords:
        return list(_iter_duckdb(path, options))

    @register_writer(".duckdb", ".db")
    def write_duckdb(rows: CanonicalRecords, path: Path, options: dict[str, Any]) -> dict[str, Any]:
        safe_cols, typed_cols, col_types, planned = _plan_duckdb_schema(iter(rows), options)
        return _write_duckdb_batches(
            rows,
            path,
            options,
            safe_columns=safe_cols,
            typed_columns=typed_cols,
            column_types=col_types,
            total=planned,
        )


_register_duckdb()
_BUILTIN_DUCKDB_READER: ReaderFn | None = READERS.get(".duckdb")
_BUILTIN_DUCKDB_WRITER: WriterFn | None = WRITERS.get(".duckdb")

