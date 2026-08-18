"""与字段模板无关的通用 PDF 文本、逐页 JSONL 和清单导出。"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any

from ..core.utils import excel_safe
from .config import ProjectConfig
from .database import Database
from .utils import atomic_output_path

INVALID_FILENAME = re.compile(r'[\\/:*?"<>|\r\n\t]')


def safe_filename(value: str, max_length: int = 100) -> str:
    value = INVALID_FILENAME.sub("_", value)
    value = re.sub(r"\s+", " ", value).strip(" .")
    return (value[:max_length].rstrip(" .") or "document")


def export_text_stage(
    config: ProjectConfig,
    db: Database,
    limit: int | None = None,
) -> dict[str, Any]:
    """Export one TXT per PDF plus auditable page JSONL and a manifest CSV."""
    if limit is not None and limit < 0:
        raise ValueError("limit 不能为负数")
    text_dir = config.output_dir / "text"
    text_dir.mkdir(parents=True, exist_ok=True)
    documents = db.fetchall(
        """
        SELECT d.doc_id, d.filename, d.primary_path, d.page_count, d.status,
               (SELECT ds.source_url FROM document_sources ds
                WHERE ds.doc_id=d.doc_id AND ds.source_url IS NOT NULL
                ORDER BY ds.id LIMIT 1) AS source_url
        FROM documents d
        WHERE EXISTS(SELECT 1 FROM pages p WHERE p.doc_id=d.doc_id)
        ORDER BY d.filename, d.doc_id
        """
    )
    if limit is not None:
        documents = documents[:limit]

    exported = 0
    pages_path = config.output_dir / "pages.jsonl"
    manifest_path = config.output_dir / "text_manifest.csv"
    page_count = 0
    with atomic_output_path(pages_path) as pages_temp, atomic_output_path(
        manifest_path
    ) as manifest_temp:
        with pages_temp.open("w", encoding="utf-8") as pages_stream, manifest_temp.open(
            "w", encoding="utf-8-sig", newline=""
        ) as manifest_stream:
            writer = csv.writer(manifest_stream)
            writer.writerow(
                ["文档ID", "文件名", "原始路径", "来源URL", "页数", "状态", "TXT路径"]
            )
            for document in documents:
                pages = db.fetchall(
                    """SELECT page_no, final_text, parse_method, needs_ocr, ocr_status,
                              ocr_confidence, printable_chars, garbled_ratio
                       FROM pages WHERE doc_id=? ORDER BY page_no""",
                    (document["doc_id"],),
                )
                if not pages:
                    continue
                stem = safe_filename(Path(document["filename"]).stem)
                text_path = text_dir / f"{stem}_{document['doc_id'][:8]}.txt"
                with atomic_output_path(text_path) as text_temp:
                    with text_temp.open("w", encoding="utf-8") as text_stream:
                        for index, page in enumerate(pages):
                            text = page["final_text"] or ""
                            if index:
                                text_stream.write("\n\n")
                            text_stream.write(
                                f"===== 第{page['page_no']}页 | "
                                f"{page['parse_method'] or 'unknown'} =====\n{text}"
                            )
                            row = {
                                "doc_id": document["doc_id"],
                                "filename": document["filename"],
                                "source_path": document["primary_path"],
                                "source_url": document["source_url"],
                                "page_no": page["page_no"],
                                "parse_method": page["parse_method"],
                                "needs_ocr": bool(page["needs_ocr"]),
                                "ocr_status": page["ocr_status"],
                                "ocr_confidence": page["ocr_confidence"],
                                "printable_chars": page["printable_chars"],
                                "garbled_ratio": page["garbled_ratio"],
                                "text": text,
                            }
                            pages_stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                            page_count += 1
                        text_stream.write("\n")
                writer.writerow(
                    [
                        excel_safe(document["doc_id"]), excel_safe(document["filename"]),
                        excel_safe(document["primary_path"]), excel_safe(document["source_url"] or ""),
                        len(pages), excel_safe(document["status"]), excel_safe(str(text_path)),
                    ]
                )
                exported += 1
    return {
        "documents": exported,
        "pages": page_count,
        "text_dir": str(text_dir),
        "pages_jsonl": str(pages_path),
        "manifest": str(manifest_path),
    }
