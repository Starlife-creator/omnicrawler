"""CSV 流式分块读写与编码严格嗅探后端。"""

from __future__ import annotations

import codecs
import csv
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from omnicrawler.core.utils import excel_safe

from ._common import (
    _DECODE_CHUNK_BYTES,
    _ENCODING_SAMPLE_BYTES,
    _PROGRESS_CHUNK,
    CanonicalRecords,
    _ensure_parent_dir,
    _ordered_columns,
    _ProgressEmitter,
    _record_rejection,
    _require_file,
    atomic_output,
    check_cancel,
    register_reader,
    register_writer,
)


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

    from omnicrawler.core.encoding import detect_encoding

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


