"""表格数据读写（支柱①通用能力）。

**这是什么**：`READERS` / `WRITERS` 注册表与五种格式（csv / jsonl / xlsx /
parquet / duckdb）的读写实现，外加 `sniff_format`。

**为什么在宿主**：`omnicrawler transform`（数据加工）需要它。这些是通用数据能力，
不是 PDF 工作台的专属实现——此前它们随 `convertx` 迁进插件，导致**未装 PDF 插件
时 transform 完全不可用**（并给出"主流程不受影响"的误导提示）。

**与插件的关系**：PDF 工作台的格式互转仍然用这一份（插件经
`document_facade.table_io()` 取用）。**只有一份实现**，不复制。

**来源**：搬运自原 `omnicrawler/convertx/_core.py` 第 1-1060 行。文档/图片转换
（`convert_image` / `convert`）仍留在插件的 `convertx` 里——那部分才是文档工作台的职责。

**可选依赖**：`pyarrow`（parquet）/ `duckdb` / `openpyxl`（xlsx）。
缺任何一个时，对应格式不注册，其余格式照常可用——不静默、不崩。
"""

from __future__ import annotations

import codecs
import csv
import importlib
import importlib.util
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

# 依赖改接：插件版的 `..compat.*` 在这里对应宿主自己的实现
# （`compat` 本就是宿主模块的副本，内容一致）。
from ..core.utils import excel_safe

LOGGER = logging.getLogger(__name__)


# ── 进度节流（原 _core.py 65-108 行）────────────────────────────

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
            # B-P3-3：用户回调异常留痕，不再静默吞掉。
            LOGGER.debug("用户进度回调抛错，已忽略: ", exc_info=True)
        self._last_emit_ts = now
        self._since_last = 0

    def flush(self, **fields: Any) -> None:
        """最后强制推一次（保证 100% 命中，避免最后不足 1 chunk 的残条不显示）。"""
        self.emit(force=True, **fields)

# ── 取消与原子写（原 convertx/_io.py，44 行，一并上提）─────────────
class ConversionCancelledError(Exception):
    """The conversion stopped before its output was committed."""


def check_cancel(options: dict[str, Any]) -> None:
    should_stop = options.get("_should_stop")
    if should_stop is not None and should_stop():
        raise ConversionCancelledError("转换已取消")


@contextmanager
def atomic_output(path: Path, options: dict[str, Any]) -> Iterator[Path]:
    """Close writer handles inside the context, then replace the target.

    The final cancellation check is the commit boundary: requests observed
    after it do not change a successful commit into a cancelled result.
    """
    check_cancel(options)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.stem}-", suffix=path.suffix, dir=path.parent)
    os.close(fd)
    temporary = Path(name)
    try:
        yield temporary
        check_cancel(options)
        temporary.replace(path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            logging.getLogger(__name__).warning("无法清理转换临时文件 %s", temporary, exc_info=True)


# ── 类型 ──────────────────────────────────────────────────
CanonicalRecords = list[dict[str, Any]]
ReaderFn = Callable[[Path, dict[str, Any]], CanonicalRecords]
WriterFn = Callable[[CanonicalRecords, Path, dict[str, Any]], dict[str, Any]]


class ConversionStatus(StrEnum):
    """转换结果状态；部分成功必须是独立状态，不能退化成布尔成功。"""

    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    COMPLETED_WITH_ERRORS = "completed_with_errors"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class ConversionIssue:
    """可行动的问题条目：``code`` 供程序判断，``message`` 供用户阅读。"""

    code: str
    message: str
    severity: str = "warning"  # info | warning | error
    detail: dict[str, Any] = field(default_factory=dict)


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
    status: ConversionStatus = ConversionStatus.COMPLETED
    issues: list[ConversionIssue] = field(default_factory=list)


# ── 注册表 ────────────────────────────────────────────────
READERS: dict[str, ReaderFn] = {}
WRITERS: dict[str, WriterFn] = {}

#: **可选**后端：扩展名 → (需要的模块, 安装用的 extra)。
#: 只为把「格式不支持」讲清楚——`_register_*()` 在缺依赖时会静默跳过注册，
#: 于是 `.parquet` 会退化成 `sniff_format() → None`，错误信息变成
#: `不支持的目标格式 None`（实测：CI 上缺 pyarrow 时就是这样，完全看不出缺什么）。
#: 注：`.xlsx` 的 openpyxl 属基础依赖，不在此列。
_OPTIONAL_BACKENDS: dict[str, tuple[str, str]] = {
    ".parquet": ("pyarrow", "omnicrawler[storage]"),
    ".duckdb": ("duckdb", "omnicrawler[storage]"),
    ".db": ("duckdb", "omnicrawler[storage]"),
}


def _missing_backend_hint(path: Path) -> str:
    """格式不被支持时，若原因是**缺可选依赖**，给出可执行的补齐命令。

    资产判据要求「缺依赖给明确降级 + 补齐命令」。只在模块**确实不可导入**时才提示，
    避免"装了却没注册"这种别的原因被误指路。
    """
    entry = _OPTIONAL_BACKENDS.get(path.suffix.lower())
    if entry is None:
        return ""
    module, extra = entry
    try:
        if importlib.util.find_spec(module) is not None:
            return ""
    except (ImportError, ValueError):
        return ""
    return f"；该格式需要可选依赖 {module}：pip install '{extra}'"


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
def normalize_format_spec(value: str | None) -> str:
    """显式格式归一：``"csv"`` / ``"CSV"`` / ``".csv"`` 都收敛为 ``".csv"``。"""
    text = (value or "").strip().lower()
    if not text:
        return ""
    return text if text.startswith(".") else f".{text}"


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
    return normalize_image_extension(suffix)


# ── 图片扩展名归一（原 convertx/formats.py，无内部依赖，一并上提）──
IMAGE_EXTENSIONS: tuple[str, ...] = (
    ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif",
)

_ALIASES = {"jpeg": ".jpg", "tif": ".tiff"}


def normalize_image_extension(value: str | None) -> str | None:
    if not value:
        return None
    text = value.strip().lower()
    if not text:
        return None
    if not text.startswith("."):
        text = f".{text}"
    text = _ALIASES.get(text.lstrip("."), text)
    return text if text in IMAGE_EXTENSIONS else None


def image_suffix(path: Path) -> str | None:
    return normalize_image_extension(path.suffix)


def is_image_pair(source: Path, target: Path, src_format: str | None, dst_format: str | None) -> bool:
    source_ext = normalize_image_extension(src_format) or image_suffix(source)
    target_ext = normalize_image_extension(dst_format) or image_suffix(target)
    return bool(source_ext and target_ext)




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
            # B-P2-18：表头列名同样经 excel_safe 中和公式注入（与数据行同口径）。
            csv.writer(fh).writerow([excel_safe(column) for column in columns])
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


def _write_xlsx_iter(
    rows: Iterable[dict[str, Any]],
    path: Path,
    options: dict[str, Any],
    *,
    columns: list[str],
    total: int,
) -> dict[str, Any]:
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font, PatternFill

    _ensure_parent_dir(path)
    pe = _ProgressEmitter(options.get("on_write_progress"))
    wb = Workbook(write_only=True)
    ws = wb.create_sheet()
    ws.title = "结构化记录"
    ws.freeze_panes = "A2"
    # B-P2-18：表头单元格统一经 excel_safe 中和公式注入（数据行已处理，补齐表头面）。
    header = [WriteOnlyCell(ws, value=excel_safe(column)) for column in columns]
    for cell in header:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    ws.append(header)
    warnings: list[str] = []
    if total > XLSX_ROW_LIMIT:
        warnings.append(
            f"XLSX 超过应用单表上限 {XLSX_ROW_LIMIT} 条，仅写前 {XLSX_ROW_LIMIT} 条，"
            f"省略 {total - XLSX_ROW_LIMIT} 条"
        )
        LOGGER.warning(warnings[-1])
    written = 0
    seen = 0
    truncated_cells = 0
    try:
        with atomic_output(path, options) as tmp:
            for row in rows:
                check_cancel(options)
                seen += 1
                if written >= XLSX_ROW_LIMIT:
                    pe.emit(written=written, total=total)
                    continue
                values = []
                for key in columns:
                    value = row.get(key, "")
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
        "omitted_records": seen - written, "truncated_cells": truncated_cells,
    }


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
        # 文档工作表的写出（`_document_ir`）属插件职责，宿主侧不处理：
        # 表格读写只管"记录 → xlsx"，文档格式由以插件内部的 convertx 处理。
        columns = _ordered_columns(rows, prefer=options.get("columns") or [])
        return _write_xlsx_iter(rows, path, options, columns=columns, total=len(rows))


_register_xlsx()
_BUILTIN_XLSX_READER: ReaderFn | None = READERS.get(".xlsx")
_BUILTIN_XLSX_WRITER: WriterFn | None = WRITERS.get(".xlsx")




# ── 图片族 ───────────────────────────────────────────────
_IMAGE_PIL_FORMATS: dict[str, str] = {
    ".png": "PNG", ".jpg": "JPEG", ".webp": "WEBP",
    ".bmp": "BMP", ".tiff": "TIFF", ".gif": "GIF",
}
_IMAGE_LOSSY = frozenset({".jpg", ".webp"})
_IMAGE_ANIMATED = frozenset({".webp", ".gif"})


