"""P3-2：ConvertX —— 任意格式互转模块（Reader × Writer = N×N 矩阵）。

**与现有 export_all 的设计取舍**（依据用户要求：如果任意格式互转比单输入多输出更实用就采纳，
且项目原有设计不一定是最好的）：

    现有 `pipeline/exporters.py::export_all` 定位 = 「pipeline 跑完后一次性导出所有格式」
    （单输入：StateStore 表 → 多输出：jsonl/csv/xlsx/parquet/duckdb 多文件同时落盘）。
    它满足 pipeline 结束那刻的"多格式输出"，但**缺少事后互转**能力：
        例 1. 用户最初只开了 CSV，后来想要 XLSX —— 以前要重跑 pipeline；
        例 2. 用户从别处拿到一份 JSONL records，想导入成自己 project 的 records.db —— 以前做不到。
        例 3. 跨系统对接：需要 Parquet/DuckDB 列式 + 压缩（AI/BI 常用），但别人只给 CSV。

    因此新增 `omnicrawler.convertx` 模块，定位 = 「文件级 A → B 互转」：
        - 公共 Reader 的兼容表示仍为 CanonicalRecords = list[flat dict]（与 export_all 展开后的
          flat records 完全一致）；已验证的内置文本路径可在 convert() 内逐条传递，不保留整表。
        - 核心入口 convert(src, dst, options)：按后缀名（或显式 fmt 指定）自动选 Reader/Writer。
        - **不影响** export_all 管道默认的多输出（用户的 config 行为保持不变），convertx 作为
          独立的 CLI/GUI 工具层提供。
        - 这是一种增量扩展，没有删除/重构任何老代码。

**Reader 注册表（按输入后缀选择）**：
    .jsonl → JSONLReader
    .csv   → CSVReader
    .parquet → ParquetReader（需要 pyarrow）
    .duckdb / .db → DuckDBReader（需要 duckdb）
    .xlsx  → XLSXReader（需要 openpyxl）

**Writer 注册表**：
    同上格式，一一对应。

**安全约束**：
    * 输入文件必须是真实存在文件（Path.resolve() → 必须在当前卷，禁止 path traversal）
    * 输出文件使用 `core.utils.atomic_write` 或等价逻辑（先写 .tmp → rename，避免半写）
    * XLSX/DuckDB 单表超过 1,000,000 行给出警告（Excel 硬限 1,048,576 行）
"""

from __future__ import annotations

import codecs
import csv
import hashlib
import json
import logging
import re
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.utils import excel_safe
from ..services.progress import (
    ProgressTracker,
    StageSpec,
    TaskProgressEvent,
)
from ._io import ConversionCancelledError, atomic_output, check_cancel

LOGGER = logging.getLogger(__name__)

# ── 进度节流：每 N 条 / 每 T ms 推一次，避免高频回调拖慢转换 ──
_PROGRESS_CHUNK: int = 250          # 至少每 250 条 1 次（小文件 2k 行也会推 8 次，视觉丝滑）
_PROGRESS_MIN_INTERVAL_S: float = 0.03  # 上限 30ms/次（大文件 2M 行也只 ~33 次/秒，GUI 完全无感）
_PROGRESS_EST_AVG_BYTES_PER_LINE: int = 180  # 读取阶段估算总行数的平均行字节启发式（含 csv 逗号/引号）
_ENCODING_SAMPLE_BYTES: int = 256 * 1024
_DECODE_CHUNK_BYTES: int = 1024 * 1024


class _ProgressEmitter:
    """节流发射器：避免每条记录都触发一次 QThread 信号。"""

    __slots__ = ("_hook", "_last_emit_ts", "_since_last")

    def __init__(self, hook: Callable[[dict[str, Any]], None] | None) -> None:
        self._hook = hook
        self._last_emit_ts: float = 0.0
        self._since_last: int = 0

    def emit(self, *, force: bool = False, **fields: Any) -> None:
        if self._hook is None:
            return
        self._since_last += 1
        now = time.monotonic()
        if not force and self._since_last < _PROGRESS_CHUNK and (now - self._last_emit_ts) < _PROGRESS_MIN_INTERVAL_S:
            return
        try:
            self._hook(dict(fields))
        except Exception:  # noqa: BLE001 — 用户回调/进度桥接报错不影响转换
            pass
        self._last_emit_ts = now
        self._since_last = 0

    def flush(self, **fields: Any) -> None:
        """最后强制推一次（保证 100% 命中，避免最后不足 1 chunk 的残条不显示）。"""
        self.emit(force=True, **fields)

__all__ = [
    "READERS",
    "WRITERS",
    "CanonicalRecords",
    "ConversionCancelledError",
    "ConvertResult",
    "ProgressTracker",
    "ReaderFn",
    "StageSpec",
    "TaskProgressEvent",
    "WriterFn",
    "convert",
    "register_reader",
    "register_writer",
    "sniff_format",
    # ---- 核心 Reader/Writer（直接调用时可导入）----
    "read_csv",
    "read_jsonl",
    "write_csv",
    "write_jsonl",
]

# ── 类型 ──────────────────────────────────────────────────
CanonicalRecords = list[dict[str, Any]]
ReaderFn = Callable[[Path, dict[str, Any]], CanonicalRecords]
WriterFn = Callable[[CanonicalRecords, Path, dict[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class ConvertResult:
    """`rows` retains the accepted-record count for existing callers.

    `extra['written_records']` reports committed rows when the writer supplies
    a count; unknown writer/rejection counts are None, never an inferred zero.
    """

    source_format: str
    target_format: str
    rows: int
    columns: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    output_path: Path | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ── 注册表 ────────────────────────────────────────────────
READERS: dict[str, ReaderFn] = {}
WRITERS: dict[str, WriterFn] = {}


def register_reader(*extensions: str) -> Callable[[ReaderFn], ReaderFn]:
    """注册一个 Reader（装饰器）。extensions 含前导点如 '.jsonl'。"""
    def _wrap(fn: ReaderFn) -> ReaderFn:
        for ext in extensions:
            READERS[ext.lower()] = fn
        return fn
    return _wrap


def register_writer(*extensions: str) -> Callable[[WriterFn], WriterFn]:
    """注册一个 Writer（装饰器）。"""
    def _wrap(fn: WriterFn) -> WriterFn:
        for ext in extensions:
            WRITERS[ext.lower()] = fn
        return fn
    return _wrap


# ── 工具 ──────────────────────────────────────────────────
def sniff_format(path: Path) -> str | None:
    """根据文件后缀推断格式（统一返回 READERS/WRITERS 中注册的 key，如 '.jsonl'）。

    Alias 归一化：
        .ndjson → .jsonl（同一 Reader/Writer）
        .db     → .duckdb
    """
    suffix = path.suffix.lower()
    if suffix == ".ndjson":
        return ".jsonl"
    if suffix == ".db":
        return ".duckdb"
    if suffix in READERS or suffix in WRITERS:
        return suffix
    return None


_BASE_COLUMNS: tuple[str, ...] = ("record_id", "source_url", "record_type", "created_at")


def _ordered_columns(rows: CanonicalRecords, *, prefer: Iterable[str] = ()) -> list[str]:
    """按 1) _BASE_COLUMNS 先；2) prefer；3) 首次出现 来稳定列序。"""
    seen: dict[str, None] = {}
    # 1) base 先
    for col in _BASE_COLUMNS:
        seen[col] = None
    # 2) prefer
    for col in prefer:
        if col:
            seen[str(col)] = None
    # 3) 首次出现
    for row in rows:
        for key in row.keys():
            seen[str(key)] = None
    return list(seen.keys())


def _require_file(path: Path) -> None:
    if not isinstance(path, Path):
        path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"ConvertX: 输入文件不存在: {path}")
    if not path.exists():
        raise FileNotFoundError(f"ConvertX: 输入文件路径无效: {path}")


# META：SQL 标识符白名单（表名/列名直插场景），仅允许 identifier 或 schema.identifier。
_SQL_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)?")


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _record_rejection(options: dict[str, Any], line: int, reason: str) -> None:
    stats = options.get("_read_stats")
    if stats is not None:
        stats["rejected_records"] += 1
        if len(stats["rejection_samples"]) < 10:
            stats["rejection_samples"].append({"line": line, "reason": reason})


def _file_decodes_strictly(path: Path, encoding: str, options: dict[str, Any]) -> bool:
    """Validate a candidate incrementally so late bad bytes cannot corrupt output."""
    try:
        decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
    except LookupError:
        return False
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_DECODE_CHUNK_BYTES):
                check_cancel(options)
                decoder.decode(chunk)
            decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return False
    return True


def _detect_csv_encoding(path: Path, options: dict[str, Any]) -> str:
    """Detect from a bounded sample, then validate the complete file by chunks."""
    with path.open("rb") as handle:
        sample = handle.read(_ENCODING_SAMPLE_BYTES)
    utf_candidate = "utf-8-sig" if sample.startswith(codecs.BOM_UTF8) else "utf-8"
    # UTF-8 is the common case and validating it by chunks is cheaper than
    # importing the optional statistical detector. Non-UTF files take the
    # compatibility detection/fallback path below.
    if _file_decodes_strictly(path, utf_candidate, options):
        return utf_candidate

    from ..core.encoding import detect_encoding

    detected = detect_encoding(sample)
    candidates = dict.fromkeys((detected, "gb18030", "latin-1"))
    for candidate in candidates:
        if _file_decodes_strictly(path, candidate, options):
            return candidate
    # latin-1 decodes every byte; this is defensive against an unavailable codec.
    raise UnicodeError(f"CSV 无法使用受支持编码解码: {path}")


# ── CSV ──────────────────────────────────────────────────
@register_reader(".csv")
def read_csv(path: Path, options: dict[str, Any]) -> CanonicalRecords:
    return list(_iter_csv(path, options))


def _iter_csv(path: Path, options: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield CSV records; the public reader keeps its historical list result."""
    _require_file(path)
    check_cancel(options)
    if "_read_stats" in options:
        options["_read_stats"]["complete"] = True
    encoding = str(options.get("encoding", "utf-8-sig"))
    on_error = str(options.get("on_error", "skip")).lower()  # skip | abort
    pe = _ProgressEmitter(options.get("on_line_progress"))
    if encoding == "auto":
        encoding = _detect_csv_encoding(path, options)
    fh = path.open("r", encoding=encoding, newline="")
    try:
        reader = csv.DictReader(fh)
        accepted = 0
        line_num = 0
        for row in reader:
            check_cancel(options)
            line_num += 1
            try:
                record = dict(row)
            except Exception as exc:  # csv 一般不含异常；保留以对齐 on_error 语义
                if on_error == "abort":
                    raise ValueError(f"CSV 解析失败（逻辑行 {line_num}）: {exc}") from exc
                _record_rejection(options, line_num, "CSV 记录无法解析")
                continue
            accepted += 1
            yield record
            pe.emit(line_num=line_num, records_so_far=accepted)
    finally:
        fh.close()
    pe.flush(line_num=line_num, records_so_far=accepted)


@register_writer(".csv")
def write_csv(rows: CanonicalRecords, path: Path, options: dict[str, Any]) -> dict[str, Any]:
    columns = _ordered_columns(rows, prefer=options.get("columns") or [])
    return _write_csv_iter(rows, path, options, columns=columns, total=len(rows))


def _write_csv_iter(
    rows: Iterable[dict[str, Any]],
    path: Path,
    options: dict[str, Any],
    *,
    columns: list[str],
    total: int | None,
) -> dict[str, Any]:
    _ensure_parent_dir(path)
    encoding = str(options.get("encoding", "utf-8-sig"))
    pe = _ProgressEmitter(options.get("on_write_progress"))
    written = 0
    truncated_cells = 0

    def safe_cell(value: Any) -> Any:
        nonlocal truncated_cells
        if isinstance(value, str) and len(value) > 32700:
            truncated_cells += 1
        return excel_safe(value)

    with atomic_output(path, options) as tmp:
        with tmp.open("w", encoding=encoding, newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            buf: list[dict[str, Any]] = []
            for row in rows:
                check_cancel(options)
                buf.append({k: safe_cell(row.get(k, "")) for k in columns})
                if len(buf) >= _PROGRESS_CHUNK:
                    writer.writerows(buf)
                    written += len(buf)
                    buf.clear()
                    pe.emit(written=written, total=total or 0)
            if buf:
                writer.writerows(buf)
                written += len(buf)
                buf.clear()
        pe.flush(written=written, total=total or 0)
    warnings = [f"CSV 有 {truncated_cells} 个单元格超过应用字符上限，内容已截断"] if truncated_cells else []
    return {"rows": written, "columns": columns, "encoding": encoding, "truncated_cells": truncated_cells, "warnings": warnings}


# ── JSONL ─────────────────────────────────────────────────
@register_reader(".jsonl", ".ndjson")
def read_jsonl(path: Path, options: dict[str, Any]) -> CanonicalRecords:
    return list(_iter_jsonl(path, options))


def _iter_jsonl(path: Path, options: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield validated JSONL records while retaining public list compatibility."""
    _require_file(path)
    check_cancel(options)
    if "_read_stats" in options:
        options["_read_stats"]["complete"] = True
    flat_mode = bool(options.get("flat", True))  # 默认把 .data 展开为 flat dict
    on_error = str(options.get("on_error", "skip")).lower()  # skip | abort
    pe = _ProgressEmitter(options.get("on_line_progress"))
    content_hasher = options.get("_content_hasher")
    accepted = 0
    last_line = 0
    with path.open("r", encoding="utf-8", newline="") as fh:
        for line_num, line in enumerate(fh, 1):
            check_cancel(options)
            if content_hasher is not None:
                content_hasher.update(line.encode("utf-8"))
            last_line = line_num
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                if on_error == "abort":
                    raise ValueError(f"JSONL 解析失败（行 {line_num}）: {e}") from e
                # skip 默认行为
                _record_rejection(options, line_num, "JSON 语法错误")
                continue
            if not isinstance(obj, dict):
                if on_error == "abort":
                    raise ValueError(f"JSONL 记录无效（行 {line_num}）: 必须是 JSON 对象")
                _record_rejection(options, line_num, "必须是 JSON 对象")
                continue
            if flat_mode and isinstance(obj.get("data"), dict):
                flat: dict[str, Any] = {
                    k: obj.get(k) for k in _BASE_COLUMNS if k in obj
                }
                _flatten_to("", obj["data"], flat)
                if obj.get("evidence"):
                    flat["evidence_json"] = json.dumps(obj["evidence"], ensure_ascii=False)
                record = flat
            else:
                record = dict(obj)
            accepted += 1
            yield record
            pe.emit(line_num=line_num, records_so_far=accepted)
    pe.flush(line_num=last_line, records_so_far=accepted)


def _iter_jsonl_with_digest(
    path: Path,
    options: dict[str, Any],
    hasher: Any,
    expected_digest: bytes,
) -> Iterator[dict[str, Any]]:
    yield from _iter_jsonl(path, options)
    if hasher.digest() != expected_digest:
        raise RuntimeError("JSONL 源文件在转换期间发生变化，已取消输出提交")


@register_writer(".jsonl", ".ndjson")
def write_jsonl(rows: CanonicalRecords, path: Path, options: dict[str, Any]) -> dict[str, Any]:
    return _write_jsonl_iter(rows, path, options, total=len(rows))


def _write_jsonl_iter(
    rows: Iterable[dict[str, Any]],
    path: Path,
    options: dict[str, Any],
    *,
    total: int | None,
) -> dict[str, Any]:
    _ensure_parent_dir(path)
    nested = bool(options.get("nested", False))  # True 时按 pipeline 原始 records.jsonl 结构
    pe = _ProgressEmitter(options.get("on_write_progress"))
    written = 0
    with atomic_output(path, options) as tmp:
        with tmp.open("w", encoding="utf-8") as fh:
            if nested:
                for row in rows:
                    check_cancel(options)
                    base = {k: row.get(k) for k in _BASE_COLUMNS if k in row}
                    data = {k: v for k, v in row.items() if k not in _BASE_COLUMNS and k != "evidence_json"}
                    evidence = {}
                    ev_raw = row.get("evidence_json")
                    if isinstance(ev_raw, str):
                        try:
                            evidence = json.loads(ev_raw)
                        except (TypeError, ValueError):
                            evidence = {"raw": ev_raw}
                    base["data"] = data
                    base["evidence"] = evidence
                    fh.write(json.dumps(base, ensure_ascii=False) + "\n")
                    written += 1
                    pe.emit(written=written, total=total or 0)
            else:
                for row in rows:
                    check_cancel(options)
                    fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
                    written += 1
                    pe.emit(written=written, total=total or 0)
        pe.flush(written=written, total=total or 0)
    return {"rows": written, "nested": nested}


def _flatten_to(prefix: str, value: Any, out: dict[str, Any]) -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, (dict, list, tuple)):
                out[key] = json.dumps(v, ensure_ascii=False, default=str)
            else:
                out[key] = v
    elif isinstance(value, (list, tuple)):
        out_key = prefix or "items"
        out[out_key] = json.dumps(value, ensure_ascii=False, default=str)
    else:
        if prefix:
            out[prefix] = value


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


# ── XLSX ─────────────────────────────────────────────────
XLSX_ROW_LIMIT = 1_000_000


def _iter_xlsx(path: Path, options: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield worksheet records from openpyxl's read-only row iterator."""
    from openpyxl import load_workbook

    _require_file(path)
    check_cancel(options)
    if "_read_stats" in options:
        options["_read_stats"]["complete"] = True
    sheet_name = options.get("sheet")
    data_only = bool(options.get("data_only", True))
    on_error = str(options.get("on_error", "skip")).lower()
    pe = _ProgressEmitter(options.get("on_line_progress"))
    workbook = load_workbook(filename=str(path), read_only=True, data_only=data_only)
    try:
        sheet = workbook[sheet_name] if sheet_name else workbook.active
        rows_iter = sheet.iter_rows(values_only=True)
        try:
            header = list(next(rows_iter))
        except StopIteration:
            pe.flush(line_num=0, records_so_far=0)
            return
        header = [str(value) if value is not None else f"col_{index}" for index, value in enumerate(header)]
        accepted = 0
        row_num = 1
        for row in rows_iter:
            check_cancel(options)
            row_num += 1
            if row is None or all(value is None or value == "" for value in row):
                continue
            try:
                record = {key: row[index] for index, key in enumerate(header) if index < len(row)}
            except Exception as exc:
                if on_error == "abort":
                    raise ValueError(f"XLSX 解析失败（行 {row_num}）: {exc}") from exc
                _record_rejection(options, row_num, "XLSX 记录无法解析")
                continue
            accepted += 1
            yield record
            pe.emit(line_num=row_num, records_so_far=accepted)
        pe.flush(line_num=row_num, records_so_far=accepted)
    finally:
        workbook.close()


def _register_xlsx() -> None:
    try:
        from openpyxl import Workbook  # noqa: F401
    except ImportError:
        return

    @register_reader(".xlsx")
    def read_xlsx(path: Path, options: dict[str, Any]) -> CanonicalRecords:
        return list(_iter_xlsx(path, options))

    @register_writer(".xlsx")
    def write_xlsx(rows: CanonicalRecords, path: Path, options: dict[str, Any]) -> dict[str, Any]:
        from openpyxl import Workbook
        from openpyxl.cell import WriteOnlyCell
        from openpyxl.styles import Font, PatternFill

        _ensure_parent_dir(path)
        columns = _ordered_columns(rows, prefer=options.get("columns") or [])
        pe = _ProgressEmitter(options.get("on_write_progress"))
        wb = Workbook(write_only=True)
        ws = wb.create_sheet()
        ws.title = "结构化记录"
        ws.freeze_panes = "A2"
        header = [WriteOnlyCell(ws, value=column) for column in columns]
        for cell in header:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1F4E78")
        ws.append(header)
        warnings: list[str] = []
        data_rows = rows
        if len(data_rows) > XLSX_ROW_LIMIT:
            warnings.append(
                f"XLSX 超过应用单表上限 {XLSX_ROW_LIMIT} 条，仅写前 {XLSX_ROW_LIMIT} 条，"
                f"省略 {len(rows) - XLSX_ROW_LIMIT} 条"
            )
            LOGGER.warning(warnings[-1])
            data_rows = data_rows[:XLSX_ROW_LIMIT]
        total = len(data_rows)
        written = 0
        truncated_cells = 0
        try:
            with atomic_output(path, options) as tmp:
                for row in data_rows:
                    check_cancel(options)
                    values = []
                    for k in columns:
                        value = row.get(k, "")
                        safe = excel_safe(value)
                        if isinstance(value, str) and (len(value) > 32700 or len(safe) > 32700):
                            truncated_cells += 1
                        if isinstance(safe, str):
                            safe = safe[:32700]
                        values.append(safe)
                    ws.append(values)
                    written += 1
                    pe.emit(written=written, total=total)
                check_cancel(options)
                wb.save(tmp)
                pe.flush(written=written, total=total)
        except (PermissionError, OSError) as exc:
            raise RuntimeError(f"无法写入 Excel 文件 {path}（可能被其他程序占用或目录不可写）: {exc}") from exc
        finally:
            if not ws.closed:
                try:
                    ws.close()
                except Exception:  # noqa: BLE001 - cleanup must not mask the conversion error
                    LOGGER.warning("无法完整关闭 XLSX 流式工作表", exc_info=True)
            wb.close()
        if truncated_cells:
            warnings.append(f"XLSX 有 {truncated_cells} 个单元格超过应用字符上限，内容已截断")
        return {
            "rows": written, "columns": columns, "warnings": warnings, "sheet": "结构化记录",
            "omitted_records": len(rows) - written, "truncated_cells": truncated_cells,
        }


_register_xlsx()
_BUILTIN_XLSX_READER: ReaderFn | None = READERS.get(".xlsx")


def _register_document() -> None:
    """注册 document 族（懒加载 document_ir，避免 import 副作用）。"""
    from . import document  # noqa: F401


_register_document()


# ── 入口 ─────────────────────────────────────────────────
def convert(
    source: str | Path,
    target: str | Path,
    *,
    src_format: str | None = None,
    dst_format: str | None = None,
    options: dict[str, Any] | None = None,
    flat: bool = True,
    nested: bool = False,
    table: str = "records",
    compression: str = "zstd",
    on_read_progress: Callable[[Any], None] | None = None,
    on_write_progress: Callable[[Any], None] | None = None,
    on_error: str = "skip",
    on_progress: Callable[[TaskProgressEvent], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> ConvertResult:
    """核心入口：A → B 互转。

    Parameters
    ----------
    source: 源文件路径
    target: 目标文件路径
    src_format: 显式指定源格式（如 '.jsonl'）；None 时按 suffix 推断
    dst_format: 显式指定目标格式；None 时按 target suffix 推断
    options:  透传给 Reader/Writer 选项（例如 nested=True 让 jsonl 输出 data/evidence 嵌套）
    flat:     JSONL 读取时是否展开 data 字段（默认 True）
    nested:   JSONL 写出时是否按 pipeline 原始嵌套结构（data/evidence 独立字段）
    table:    DuckDB 读写时使用的表名（默认 'records'）
    compression: Parquet 写出时压缩算法（zstd/snappy/gzip/none，默认 zstd）
    on_read_progress:  读取阶段进度回调（接收 dict: {line_num, records_so_far}）
    on_write_progress: 写入阶段进度回调（旧式 dict hooks，保持兼容）
    on_error: 解析错误策略 'skip'（默认）| 'abort'
    should_stop: 协作取消检查；内置组件在读写期间及提交前检查。
                 第三方不可中断操作需等返回；自定义 Reader/Writer 可通过
                 options['_should_stop'] 接入同一检查。
    on_progress: **统一进度事件回调**（推荐）。接收 ``TaskProgressEvent``：
                 分阶段路径为 read(60%) / write(40%)；单遍流式路径为 convert(100%)。
                 事件包含 EMA ETA、瞬时速率和状态机。

    Raises
    ------
    FileNotFoundError: 源文件不存在
    KeyError: 源或目标格式未在注册表中注册（通常是缺少 openpyxl/pyarrow/duckdb 等可选依赖）
    """
    src = source if isinstance(source, Path) else Path(source)
    dst = target if isinstance(target, Path) else Path(target)
    if src.resolve() == dst.resolve():
        raise ValueError(f"ConvertX: 源与目标路径相同，拒绝覆盖: {src}")

    opts = dict(options or {})
    src_fmt = (src_format or "").lower() or sniff_format(src)
    dst_fmt = (dst_format or "").lower() or sniff_format(dst)
    if not src_fmt or src_fmt not in READERS:
        raise KeyError(f"ConvertX: 不支持的源格式 {src_fmt!r}（已注册: {sorted(READERS)}）")
    if not dst_fmt or dst_fmt not in WRITERS:
        raise KeyError(f"ConvertX: 不支持的目标格式 {dst_fmt!r}（已注册: {sorted(WRITERS)}）")
    reader_key = f"reader{src_fmt.replace('.', '_')}"
    writer_key = f"writer{dst_fmt.replace('.', '_')}"
    jsonl_output_stream_path = (
        dst_fmt == ".jsonl"
        and WRITERS[dst_fmt] is write_jsonl
        and (
            (
                src_fmt == ".csv"
                and READERS[src_fmt] is read_csv
            )
            or (src_fmt == ".jsonl" and READERS[src_fmt] is read_jsonl)
            or (
                src_fmt == ".xlsx"
                and _BUILTIN_XLSX_READER is not None
                and READERS[src_fmt] is _BUILTIN_XLSX_READER
            )
            or (
                src_fmt == ".parquet"
                and _BUILTIN_PARQUET_READER is not None
                and READERS[src_fmt] is _BUILTIN_PARQUET_READER
            )
            or (
                src_fmt == ".duckdb"
                and _BUILTIN_DUCKDB_READER is not None
                and READERS[src_fmt] is _BUILTIN_DUCKDB_READER
            )
        )
    )
    jsonl_csv_stream_path = (
        src_fmt in {".jsonl", ".ndjson"}
        and READERS[src_fmt] is read_jsonl
        and dst_fmt == ".csv"
        and WRITERS[dst_fmt] is write_csv
    )
    jsonl_parquet_stream_path = (
        src_fmt in {".jsonl", ".ndjson"}
        and READERS[src_fmt] is read_jsonl
        and dst_fmt == ".parquet"
        and _BUILTIN_PARQUET_WRITER is not None
        and WRITERS[dst_fmt] is _BUILTIN_PARQUET_WRITER
    )
    jsonl_duckdb_stream_path = (
        src_fmt in {".jsonl", ".ndjson"}
        and READERS[src_fmt] is read_jsonl
        and dst_fmt == ".duckdb"
        and _BUILTIN_DUCKDB_WRITER is not None
        and WRITERS[dst_fmt] is _BUILTIN_DUCKDB_WRITER
    )
    stream_path = (
        jsonl_output_stream_path
        or jsonl_csv_stream_path
        or jsonl_parquet_stream_path
        or jsonl_duckdb_stream_path
    )

    # ── 统一进度 tracker（仅在 on_progress 显式传入时启用，避免副作用）──
    tracker: ProgressTracker | None = None
    if on_progress is not None:
        tracker = ProgressTracker(
            stages=(
                [StageSpec("convert", weight=100, display_name="转换格式", has_items=True)]
                if stream_path
                else [
                    StageSpec("read", weight=60, display_name="读取格式", has_items=True),
                    StageSpec("write", weight=40, display_name="写出格式", has_items=True),
                ]
            ),
            on_event=on_progress,
        )
        tracker.start()

    def _wrapped_read_hook(payload: dict[str, Any]) -> None:
        # 1) 先调用用户原始 hook（若有）
        if on_read_progress is not None:
            try:
                on_read_progress(payload)
            except Exception:  # noqa: BLE001 — hook 错误不得中断转换
                pass
        # 2) 再更新 tracker：records_so_far 当前处理条数
        if tracker is not None and not stream_path:
            records_so_far = int(payload.get("records_so_far") or 0)
            tracker.set_item_progress(records_so_far, max(records_so_far, tracker._items_total or 0))  # type: ignore[attr-defined]

    def _wrapped_write_hook(payload: dict[str, Any]) -> None:
        if on_write_progress is not None:
            try:
                on_write_progress(payload)
            except Exception:  # noqa: BLE001
                pass
        if tracker is not None and not stream_path:
            current = int(payload.get("written") or payload.get("records_written") or 0)
            total = int(payload.get("total") or tracker._items_total or current)  # type: ignore[attr-defined]
            tracker.set_item_progress(current, total)

    # Reader 通用选项：按实际 reader_key 单点注入即可（不再对不存在的 key 做 fallback）
    r_opts = dict(opts.get(reader_key) or {})
    opts[reader_key] = r_opts
    read_stats: dict[str, Any] = {"complete": False, "rejected_records": 0, "rejection_samples": []}
    r_opts["_read_stats"] = read_stats
    r_opts["_should_stop"] = should_stop
    r_opts.setdefault("flat", flat)
    r_opts.setdefault("on_error", on_error)
    if tracker is not None or on_read_progress is not None:
        r_opts["on_line_progress"] = _wrapped_read_hook

    # Writer 通用选项：单点注入 + 格式特定默认
    w_opts = dict(opts.get(writer_key) or {})
    opts[writer_key] = w_opts
    w_opts["_should_stop"] = should_stop
    w_opts.setdefault("nested", nested)
    if tracker is not None or on_write_progress is not None:
        w_opts["on_write_progress"] = _wrapped_write_hook
    if dst_fmt in {".parquet"}:
        w_opts.setdefault("compression", compression)
    if dst_fmt in {".duckdb", ".db"}:
        w_opts.setdefault("table", table)
    if src_fmt in {".duckdb", ".db"}:
        r_opts.setdefault("table", table)

    est_read_items: int = 0
    if tracker is not None and not stream_path:
        if src_fmt in {".csv", ".jsonl", ".ndjson"}:
            try:
                est_read_items = max(1, src.stat().st_size // _PROGRESS_EST_AVG_BYTES_PER_LINE)
            except OSError:
                est_read_items = 0

    if stream_path:
        if tracker is not None:
            # CSV 的逻辑记录数不能由字节数准确推导（字段可含换行）；保持不定进度，
            # 由旧式 hook 报告已处理条数，完成提交后再推进到 100%。
            tracker.begin_stage("convert")
        try:
            seen_columns: dict[str, None] = {}

            if jsonl_csv_stream_path:
                scan_hasher = hashlib.sha256()
                r_opts["_content_hasher"] = scan_hasher
                accepted_count = 0
                for row in _iter_jsonl(src, r_opts):
                    for key in row:
                        seen_columns[str(key)] = None
                    accepted_count += 1
                expected_digest = scan_hasher.digest()
                preferred_columns = w_opts.get("columns") or opts.get("columns") or []
                cols = _ordered_columns([], prefer=[*preferred_columns, *seen_columns])

                write_hasher = hashlib.sha256()
                second_read_opts = dict(r_opts)
                second_read_opts.pop("_read_stats", None)
                second_read_opts.pop("on_line_progress", None)
                second_read_opts["_content_hasher"] = write_hasher

                writer_meta = _write_csv_iter(
                    _iter_jsonl_with_digest(src, second_read_opts, write_hasher, expected_digest),
                    dst,
                    w_opts,
                    columns=cols,
                    total=accepted_count,
                )
            elif jsonl_parquet_stream_path:
                scan_hasher = hashlib.sha256()
                r_opts["_content_hasher"] = scan_hasher
                schema, accepted_count = _plan_parquet_schema(_iter_jsonl(src, r_opts), r_opts)
                expected_digest = scan_hasher.digest()

                write_hasher = hashlib.sha256()
                second_read_opts = dict(r_opts)
                second_read_opts.pop("_read_stats", None)
                second_read_opts.pop("on_line_progress", None)
                second_read_opts["_content_hasher"] = write_hasher

                writer_meta = _write_parquet_batches(
                    _iter_jsonl_with_digest(src, second_read_opts, write_hasher, expected_digest),
                    dst,
                    w_opts,
                    schema=schema,
                    total=accepted_count,
                )
                cols = list(schema.names)
            elif jsonl_duckdb_stream_path:
                scan_hasher = hashlib.sha256()
                r_opts["_content_hasher"] = scan_hasher
                safe_columns, typed_columns, column_types, accepted_count = _plan_duckdb_schema(
                    _iter_jsonl(src, r_opts), r_opts
                )
                expected_digest = scan_hasher.digest()

                write_hasher = hashlib.sha256()
                second_read_opts = dict(r_opts)
                second_read_opts.pop("_read_stats", None)
                second_read_opts.pop("on_line_progress", None)
                second_read_opts["_content_hasher"] = write_hasher

                writer_meta = _write_duckdb_batches(
                    _iter_jsonl_with_digest(src, second_read_opts, write_hasher, expected_digest),
                    dst,
                    w_opts,
                    safe_columns=safe_columns,
                    typed_columns=typed_columns,
                    column_types=column_types,
                    total=accepted_count,
                )
                cols = safe_columns
            else:
                def rows_with_columns() -> Iterator[dict[str, Any]]:
                    if src_fmt == ".csv":
                        source_rows = _iter_csv(src, r_opts)
                    elif src_fmt == ".jsonl":
                        source_rows = _iter_jsonl(src, r_opts)
                    elif src_fmt == ".xlsx":
                        source_rows = _iter_xlsx(src, r_opts)
                    elif src_fmt == ".parquet":
                        source_rows = _iter_parquet(src, r_opts)
                    else:
                        source_rows = _iter_duckdb(src, r_opts)
                    for row in source_rows:
                        for key in row:
                            seen_columns[str(key)] = None
                        yield row

                writer_meta = _write_jsonl_iter(rows_with_columns(), dst, w_opts, total=None)
                cols = _ordered_columns(
                    [], prefer=[*(opts.get("columns") or []), *seen_columns]
                )
        except (ConversionCancelledError, KeyboardInterrupt):
            if tracker is not None:
                tracker.cancel()
            raise
        except Exception:
            if tracker is not None:
                tracker.fail("转换阶段出错")
            raise
        if not jsonl_csv_stream_path:
            accepted_count = int(writer_meta.get("rows", 0))
        if tracker is not None:
            tracker.end_stage("convert")
            tracker.finish()
    else:
        # ── 阶段 1：Read（权重 60%）──────────────────────────
        if tracker is not None:
            tracker.begin_stage("read", expected_items=est_read_items)
        try:
            check_cancel(r_opts)
            rows = READERS[src_fmt](src, r_opts)
            check_cancel(r_opts)
        except (ConversionCancelledError, KeyboardInterrupt):
            if tracker is not None:
                tracker.cancel()
            raise
        except Exception:
            if tracker is not None:
                tracker.fail("读取阶段出错")
            raise
        if tracker is not None:
            tracker.end_stage("read")

        accepted_count = len(rows)
        cols = _ordered_columns(rows, prefer=opts.get("columns") or [])

        # ── 阶段 2：Write（权重 40%）─────────────────────────
        if tracker is not None:
            tracker.begin_stage("write", expected_items=accepted_count)
        w_opts.setdefault("columns", cols)
        try:
            check_cancel(w_opts)
            writer_meta = WRITERS[dst_fmt](rows, dst, w_opts)
        except (ConversionCancelledError, KeyboardInterrupt):
            if tracker is not None:
                tracker.cancel()
            raise
        except Exception:
            if tracker is not None:
                tracker.fail("写入阶段出错")
            raise
        if tracker is not None:
            tracker.end_stage("write")
            tracker.finish()

    writer_meta = dict(writer_meta) if isinstance(writer_meta, dict) else {}
    warnings: list[str] = list(writer_meta.pop("warnings", []))
    rejected = read_stats["rejected_records"] if read_stats["complete"] else None
    if rejected:
        examples = "；".join(
            f"第 {sample['line']} 行：{sample['reason']}" for sample in read_stats["rejection_samples"][:3]
        )
        warnings.insert(0, f"读取时跳过 {rejected} 条无效记录（示例：{examples}）")
    written = writer_meta.get("rows")
    writer_meta.update(
        accepted_records=accepted_count,
        written_records=written if isinstance(written, int) and not isinstance(written, bool) else None,
        rejected_records=rejected,
        rejection_samples=read_stats["rejection_samples"],
    )
    return ConvertResult(
        source_format=src_fmt,
        target_format=dst_fmt,
        rows=accepted_count,
        columns=cols,
        warnings=warnings,
        output_path=dst,
        extra=writer_meta,
    )
