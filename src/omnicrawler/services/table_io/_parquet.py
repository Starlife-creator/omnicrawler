"""Apache Parquet 列式分批读写后端（可选依赖 pyarrow）。"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from ._common import (
    _PROGRESS_CHUNK,
    READERS,
    WRITERS,
    CanonicalRecords,
    ReaderFn,
    WriterFn,
    _ensure_parent_dir,
    _ProgressEmitter,
    _require_file,
    atomic_output,
    check_cancel,
    register_reader,
    register_writer,
)


# ── Parquet ───────────────────────────────────────────────
def _iter_parquet(path: Path, options: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield Parquet batches without retaining the complete Python row list."""
    import pyarrow.parquet as pq

    _require_file(path)
    check_cancel(options)
    if "_read_stats" in options:
        options["_read_stats"]["complete"] = True
    pe = _ProgressEmitter(options.get("on_line_progress"))
    accepted = 0
    row_cursor = 0
    with pq.ParquetFile(path) as parquet_file:
        total_rows = int(getattr(parquet_file.metadata, "num_rows", 0) or 0)
        for batch in parquet_file.iter_batches(batch_size=max(1, _PROGRESS_CHUNK)):
            check_cancel(options)
            for value in batch.to_pylist():
                if isinstance(value, dict):
                    accepted += 1
                    yield value
            row_cursor += batch.num_rows
            pe.emit(line_num=row_cursor, records_so_far=accepted, total_rows=total_rows)
    pe.flush(line_num=row_cursor, records_so_far=accepted, total_rows=total_rows)


def _parquet_chunk_table(chunk: list[dict[str, Any]]) -> Any:
    """Build a table after discovering every top-level key in a bounded chunk."""
    import pyarrow as pa

    columns: dict[str, None] = {}
    for row in chunk:
        for column in row:
            if not isinstance(column, str):
                raise ValueError("Parquet 字段名必须是字符串")
            columns.setdefault(column, None)
    return pa.table({column: pa.array([row.get(column) for row in chunk]) for column in columns})


def _plan_parquet_schema(
    rows: Iterable[dict[str, Any]], options: dict[str, Any]
) -> tuple[Any, int]:
    """Scan bounded chunks and return a complete, compatible Arrow schema and count."""
    import pyarrow as pa

    schema: Any = None
    accepted = 0
    chunk: list[dict[str, Any]] = []

    def merge_chunk(values: list[dict[str, Any]]) -> None:
        nonlocal schema
        if not values:
            return
        try:
            chunk_schema = _parquet_chunk_table(values).schema
            schema = (
                chunk_schema
                if schema is None
                else pa.unify_schemas([schema, chunk_schema], promote_options="permissive")
            )
        except (pa.ArrowException, TypeError, ValueError) as exc:
            raise ValueError(f"Parquet 字段类型不兼容，无法安全写入: {exc}") from exc

    for row in rows:
        check_cancel(options)
        chunk.append(row)
        accepted += 1
        if len(chunk) >= _PROGRESS_CHUNK:
            merge_chunk(chunk)
            chunk.clear()
    merge_chunk(chunk)
    if schema is None:
        schema = pa.schema([pa.field("record_id", pa.string())])
    if len(schema) == 0:
        # Parquet 无法表达“有行但零列”；沿用空输出的最小占位列以保留行数。
        schema = pa.schema([pa.field("record_id", pa.string())])
    return schema, accepted


def _write_parquet_batches(
    rows: Iterable[dict[str, Any]],
    path: Path,
    options: dict[str, Any],
    *,
    schema: Any,
    total: int,
) -> dict[str, Any]:
    """Write already-planned rows while retaining only one bounded batch."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    _ensure_parent_dir(path)
    pe = _ProgressEmitter(options.get("on_write_progress"))
    compression = str(options.get("compression", "zstd"))
    written = 0
    chunk: list[dict[str, Any]] = []
    with atomic_output(path, options) as tmp:
        with pq.ParquetWriter(tmp, schema, compression=compression) as writer:
            for row in rows:
                check_cancel(options)
                chunk.append(row)
                if len(chunk) < _PROGRESS_CHUNK:
                    continue
                try:
                    batch_table = pa.Table.from_pylist(chunk, schema=schema)
                except (pa.ArrowException, TypeError, ValueError) as exc:
                    raise ValueError(f"Parquet 数据无法按规划类型写入: {exc}") from exc
                writer.write_table(batch_table)
                written += len(chunk)
                chunk.clear()
                pe.emit(written=written, total=total)
            if chunk:
                try:
                    batch_table = pa.Table.from_pylist(chunk, schema=schema)
                except (pa.ArrowException, TypeError, ValueError) as exc:
                    raise ValueError(f"Parquet 数据无法按规划类型写入: {exc}") from exc
                writer.write_table(batch_table)
                written += len(chunk)
                chunk.clear()
        pe.flush(written=written, total=total)
    return {"rows": written, "columns": list(schema.names), "compression": compression}


def _register_parquet() -> None:
    try:
        import pyarrow as pa  # noqa: F401
        import pyarrow.parquet as pq  # noqa: F401
    except ImportError:
        return

    @register_reader(".parquet")
    def read_parquet(path: Path, options: dict[str, Any]) -> CanonicalRecords:
        return list(_iter_parquet(path, options))

    @register_writer(".parquet")
    def write_parquet(rows: CanonicalRecords, path: Path, options: dict[str, Any]) -> dict[str, Any]:
        import pyarrow as pa
        import pyarrow.parquet as pq

        _ensure_parent_dir(path)
        compression = str(options.get("compression", "zstd"))

        if not rows:
            pe = _ProgressEmitter(options.get("on_write_progress"))
            schema = pa.schema([pa.field("record_id", pa.string())])
            empty_table = pa.table({"record_id": pa.array([], type=pa.string())}, schema=schema)
            with atomic_output(path, options) as tmp:
                pq.write_table(empty_table, tmp, compression=compression)
                pe.flush(written=0, total=0)
            return {"rows": 0, "columns": schema.names, "compression": compression}

        schema, planned = _plan_parquet_schema(iter(rows), options)
        return _write_parquet_batches(rows, path, options, schema=schema, total=planned)


_register_parquet()
_BUILTIN_PARQUET_READER: ReaderFn | None = READERS.get(".parquet")
_BUILTIN_PARQUET_WRITER: WriterFn | None = WRITERS.get(".parquet")

