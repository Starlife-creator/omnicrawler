"""JSON Lines 逐行流式读写与嵌套键展平后端。"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from ._common import (
    _BASE_COLUMNS,
    CanonicalRecords,
    _ensure_parent_dir,
    _ProgressEmitter,
    _record_rejection,
    _require_file,
    atomic_output,
    check_cancel,
    register_reader,
    register_writer,
)


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

