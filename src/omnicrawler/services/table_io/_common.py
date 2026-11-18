"""表格数据读写公共契约、进度节流器、原子写与格式注册表。"""

from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import re
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

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


# ── 图片族 ───────────────────────────────────────────────
_IMAGE_PIL_FORMATS: dict[str, str] = {
    ".png": "PNG", ".jpg": "JPEG", ".webp": "WEBP",
    ".bmp": "BMP", ".tiff": "TIFF", ".gif": "GIF",
}
_IMAGE_LOSSY = frozenset({".jpg", ".webp"})
_IMAGE_ANIMATED = frozenset({".webp", ".gif"})


