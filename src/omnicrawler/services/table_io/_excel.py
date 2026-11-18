"""Excel (.xlsx) 流式只读/只写后端（可选依赖 openpyxl，含公式注入防护）。"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from omnicrawler.core.utils import excel_safe

from ._common import (
    LOGGER,
    READERS,
    WRITERS,
    CanonicalRecords,
    ReaderFn,
    WriterFn,
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


