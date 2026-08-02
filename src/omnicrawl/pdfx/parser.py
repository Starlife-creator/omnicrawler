from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import fitz

from .config import ProjectConfig
from .database import Database
from .utils import clean_text, utcnow

PARSER_VERSION = "native-1.0"


def text_quality(text: str) -> tuple[int, float]:
    printable = sum(1 for char in text if char.isprintable() and not char.isspace())
    if not text:
        return 0, 1.0
    bad = text.count("\ufffd") + text.count("\x00")
    control = sum(1 for char in text if ord(char) < 32 and char not in "\n\r\t")
    ratio = min(1.0, (bad + control) / max(1, len(text)))
    return printable, ratio


def parse_document(path: str, min_chars: int, max_garbled_ratio: float) -> dict[str, Any]:
    pages: list[dict[str, Any]] = []
    with fitz.open(path) as document:
        if document.needs_pass:
            raise PermissionError("PDF需要密码")
        for page_index, page in enumerate(document):
            raw_text = page.get_text("text", sort=True)
            text = clean_text(raw_text)
            printable, garbled = text_quality(text)
            needs_ocr = printable < min_chars or garbled > max_garbled_ratio
            rect = page.rect
            pages.append({
                "page_no": page_index + 1,
                "width": float(rect.width),
                "height": float(rect.height),
                "native_text": text,
                "final_text": text,
                "parse_method": "native",
                "printable_chars": printable,
                "garbled_ratio": garbled,
                "needs_ocr": int(needs_ocr),
                "ocr_status": "pending" if needs_ocr else "not_needed",
            })
    return {"page_count": len(pages), "pages": pages}


def parse_stage(
    config: ProjectConfig,
    db: Database,
    limit: int | None = None,
    workers: int | None = None,
) -> dict[str, int]:
    if limit is not None and limit < 0:
        raise ValueError("limit 不能为负数")
    parser_config = config.parser
    min_chars = int(parser_config.get("min_native_chars", 40))
    max_garbled = float(parser_config.get("max_garbled_ratio", 0.03))
    workers = int(parser_config.get("workers", 4)) if workers is None else workers
    if not 1 <= workers <= 64:
        raise ValueError("workers 必须在1到64之间")
    rows = db.fetchall(
        "SELECT doc_id, primary_path FROM documents WHERE status IN ('ingested','parse_failed') ORDER BY filename"
    )
    if limit is not None:
        rows = rows[:limit]
    summary = {"selected": len(rows), "parsed": 0, "failed": 0, "ocr_pages": 0}
    if not rows:
        return summary

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(parse_document, row["primary_path"], min_chars, max_garbled): row
            for row in rows
        }
        for future in as_completed(futures):
            row = futures[future]
            doc_id = row["doc_id"]
            try:
                result = future.result()
                now = utcnow()
                with db.transaction() as conn:
                    conn.execute("DELETE FROM pages WHERE doc_id=?", (doc_id,))
                    conn.executemany(
                        """
                        INSERT INTO pages(
                            doc_id, page_no, width, height, native_text, final_text,
                            parse_method, printable_chars, garbled_ratio, needs_ocr,
                            ocr_status, updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        [
                            (
                                doc_id, page["page_no"], page["width"], page["height"],
                                page["native_text"], page["final_text"], page["parse_method"],
                                page["printable_chars"], page["garbled_ratio"],
                                page["needs_ocr"], page["ocr_status"], now,
                            )
                            for page in result["pages"]
                        ],
                    )
                    ocr_pages = sum(page["needs_ocr"] for page in result["pages"])
                    status = "parsed" if ocr_pages == 0 else "parsed_native"
                    conn.execute(
                        """
                        UPDATE documents SET page_count=?, status=?, text_page_count=?,
                            ocr_page_count=0, error=NULL, parser_version=?, updated_at=?
                        WHERE doc_id=?
                        """,
                        (
                            result["page_count"], status,
                            result["page_count"] - ocr_pages, PARSER_VERSION, now, doc_id,
                        ),
                    )
                summary["parsed"] += 1
                summary["ocr_pages"] += ocr_pages
            except Exception as exc:  # noqa: BLE001 - per-file isolation is intentional
                summary["failed"] += 1
                db.add_error(doc_id, "parse", exc)
                db.execute(
                    "UPDATE documents SET status='parse_failed', error=?, updated_at=? WHERE doc_id=?",
                    (str(exc)[:4000], utcnow(), doc_id),
                )
    return summary


def render_page(path: str, page_no: int, dpi: int = 220) -> bytes:
    with fitz.open(path) as document:
        page = document.load_page(page_no - 1)
        pixmap = page.get_pixmap(dpi=dpi, alpha=False)
        return pixmap.tobytes("png")
