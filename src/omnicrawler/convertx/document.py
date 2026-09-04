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

from . import CanonicalRecords, _ensure_parent_dir, register_reader, register_writer
from ._io import atomic_output, check_cancel

_DOCUMENT_EXTENSIONS = (
    ".txt", ".md", ".html", ".htm", ".eml",
    ".docx", ".pptx", ".odt", ".epub",
)


def _parse_rows(path: Path, options: dict[str, Any]) -> CanonicalRecords:
    """懒加载 document_ir 并把文档解析为单条记录。"""
    from ..document_ir import parse_document

    check_cancel(options)
    doc = parse_document(path, options, on_progress=options.get("on_progress"))
    check_cancel(options)
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
