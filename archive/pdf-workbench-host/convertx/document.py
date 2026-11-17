"""ConvertX document 族：任意文档 → 文本/Markdown 导出（document_ir 桥接）。

- Reader：把 .txt/.md/.html/.htm/.docx/.pptx/.odt/.epub/.eml 解析为单条记录
  （title + text），复用 document_ir.parse_document，懒加载避免 import 副作用。
- Writer：.txt（纯文本）/ .md（Markdown）从记录导出。

依赖：富文档格式（docx/pptx）依赖 python-docx/python-pptx，缺失时
parse_document 抛 ModuleNotFoundError（由 CLI 捕获提示 omnicrawler[document]）。
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from ..document_ir.writers import (
    ir_from_records,
)
from ..document_ir.writers import (
    write_docx as write_ir_docx,
)
from ..document_ir.writers import (
    write_html as write_ir_html,
)
from ..document_ir.writers import (
    write_pdf as write_ir_pdf,
)
from ..document_ir.writers import (
    write_xlsx as write_ir_xlsx,
)
from ._core import CanonicalRecords, _ensure_parent_dir, register_reader, register_writer
from ._io import atomic_output, check_cancel

_DOCUMENT_EXTENSIONS = (
    ".txt", ".md", ".html", ".htm", ".eml",
    ".docx", ".pptx", ".odt", ".epub", ".pdf",
)


def _cached_ir(options: dict[str, Any]) -> Any:
    shared = options.get("_shared_options") or {}
    return shared.get("_document_ir")


def _ir_for(rows: CanonicalRecords, options: dict[str, Any]) -> Any:
    cached = _cached_ir(options)
    if cached is not None:
        return cached
    raw_source = options.get("_source_path")
    return ir_from_records(
        rows,
        title=str(options.get("title") or ""),
        source=Path(raw_source) if raw_source else None,
    )


def _parse_rows(path: Path, options: dict[str, Any]) -> CanonicalRecords:
    """懒加载 document_ir 并把文档解析为单条记录。"""
    from ..document_ir import parse_document

    check_cancel(options)
    doc = parse_document(path, options, on_progress=options.get("on_progress"))
    check_cancel(options)
    shared = options.get("_shared_options")
    if shared is not None:
        # 文档 → 文档/表格的写出阶段复用同一份 IR，避免表格与标题层级退化为纯文本。
        shared["_document_ir"] = doc
    if "_read_stats" in options:
        options["_read_stats"]["complete"] = True
    return [{
        "record_id": f"doc-{doc.kind.lstrip('.')}",
        "source_url": str(path),
        "record_type": "document",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "title": doc.title,
        "text": doc.to_text(),
        "kind": doc.kind,
    }]


@register_reader(*_DOCUMENT_EXTENSIONS)
def read_document(path: Path, options: dict[str, Any]) -> CanonicalRecords:
    return _parse_rows(path, options)


@register_writer(".txt")
def write_text(rows: CanonicalRecords, path: Path, options: dict[str, Any]) -> dict[str, Any]:
    _ensure_parent_dir(path)
    cached = _cached_ir(options)
    if cached is not None:
        content = cached.to_text()
        with atomic_output(path, options) as tmp:
            tmp.write_text(content, encoding=str(options.get("encoding", "utf-8")))
        return {"rows": len(rows), "chars": len(content), "tables": cached.table_count()}
    blocks: list[str] = []
    for row in rows:
        check_cancel(options)
        title = str(row.get("title") or "").strip()
        text = str(row.get("text") or "").strip()
        if title and text:
            blocks.append(f"{title}\n\n{text}")
        elif text:
            blocks.append(text)
        elif title:
            blocks.append(title)
    content = "\n\n---\n\n".join(blocks)
    with atomic_output(path, options) as tmp:
        tmp.write_text(content, encoding="utf-8")
    return {"rows": len(rows), "chars": len(content)}


@register_writer(".md")
def write_markdown(rows: CanonicalRecords, path: Path, options: dict[str, Any]) -> dict[str, Any]:
    _ensure_parent_dir(path)
    cached = _cached_ir(options)
    if cached is not None:
        content = cached.to_markdown()
        with atomic_output(path, options) as tmp:
            tmp.write_text(content, encoding=str(options.get("encoding", "utf-8")))
        return {"rows": len(rows), "chars": len(content), "tables": cached.table_count()}
    blocks: list[str] = []
    for row in rows:
        check_cancel(options)
        title = str(row.get("title") or "").strip()
        text = str(row.get("text") or "").strip()
        if title:
            blocks.append(f"# {title}")
        if text:
            blocks.append(text)
    content = "\n\n---\n\n".join(blocks)
    with atomic_output(path, options) as tmp:
        tmp.write_text(content, encoding="utf-8")
    return {"rows": len(rows), "chars": len(content)}


def _write_ir(
    rows: CanonicalRecords,
    path: Path,
    options: dict[str, Any],
    writer: Any,
) -> dict[str, Any]:
    _ensure_parent_dir(path)
    ir = _ir_for(rows, options)
    with atomic_output(path, options) as tmp:
        meta = writer(ir, tmp, options)
    return dict(meta)


@register_writer(".html", ".htm")
def write_html(rows: CanonicalRecords, path: Path, options: dict[str, Any]) -> dict[str, Any]:
    return _write_ir(rows, path, options, write_ir_html)


@register_writer(".docx")
def write_docx(rows: CanonicalRecords, path: Path, options: dict[str, Any]) -> dict[str, Any]:
    return _write_ir(rows, path, options, write_ir_docx)


@register_writer(".pdf")
def write_pdf(rows: CanonicalRecords, path: Path, options: dict[str, Any]) -> dict[str, Any]:
    return _write_ir(rows, path, options, write_ir_pdf)


def write_document_xlsx(
    rows: CanonicalRecords,
    path: Path,
    options: dict[str, Any],
) -> dict[str, Any]:
    """文档 IR → XLSX（多工作表）。由 ``_core.write_xlsx`` 在 IR 存在时分派。"""
    return _write_ir(rows, path, options, write_ir_xlsx)
