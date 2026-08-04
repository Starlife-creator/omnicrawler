from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import fitz

from .config import ProjectConfig
from .database import Database
from .utils import clean_text, utcnow

PARSER_VERSION = "native-1.0"

# D16：同一文档解析失败最大次数，超过后置 parse_dead 排除
MAX_PARSE_ATTEMPTS = 3


def text_quality(text: str) -> tuple[int, float]:
    printable = sum(1 for char in text if char.isprintable() and not char.isspace())
    if not text:
        return 0, 1.0
    bad = text.count("\ufffd") + text.count("\x00")
    control = sum(1 for char in text if ord(char) < 32 and char not in "\n\r\t")
    ratio = min(1.0, (bad + control) / max(1, len(text)))
    return printable, ratio


def _image_coverage_ratio(page) -> float:
    """页面图片总面积占比（D12：纯图表格页夹带页眉页脚时强制 OCR）。"""
    try:
        images = page.get_images(full=True)
        if not images:
            return 0.0
        rect = page.rect
        page_area = max(float(rect.width) * float(rect.height), 1.0)
        covered = 0.0
        for image_info in images:
            for image_rect in page.get_image_rects(image_info[0]):
                covered += float(image_rect.width) * float(image_rect.height)
        return min(covered / page_area, 1.0)
    except Exception:  # noqa: BLE001 - 判据失败按无图处理
        return 0.0


def _iter_parsed_pages(path: str, min_chars: int, max_garbled_ratio: float):
    """D36：逐页 yield 解析结果，避免整文档 pages 列表常驻内存（数千页大文件内存峰值受控）。"""
    with fitz.open(path) as document:
        if document.needs_pass:
            raise PermissionError("PDF需要密码")
        for page_index, page in enumerate(document):
            raw_text = page.get_text("text", sort=True)
            text = clean_text(raw_text, compress_ws=False)  # D11：保留原始空白（表格列对齐信号），检索时再压缩
            printable, garbled = text_quality(text)
            needs_ocr = printable < min_chars or garbled > max_garbled_ratio
            if not needs_ocr and _image_coverage_ratio(page) > 0.6:
                # D12：页面图像覆盖超 60%（纯图表格页夹带页眉页脚误判有文字层）→ 强制 OCR
                needs_ocr = True
            final_text = text
            if not needs_ocr:
                # D8：原生文字层表格结构恢复（find_tables → Markdown），期初/期末等列归属不再丢失
                table_md = _extract_tables_markdown(page)
                if table_md:
                    final_text = f"{text}\n\n[表格结构]\n{table_md}".strip()
            rect = page.rect
            yield {
                "page_no": page_index + 1,
                "width": float(rect.width),
                "height": float(rect.height),
                "native_text": text,
                "final_text": final_text,
                "parse_method": "native",
                "printable_chars": printable,
                "garbled_ratio": garbled,
                "needs_ocr": int(needs_ocr),
                "ocr_status": "pending" if needs_ocr else "not_needed",
            }


def parse_document(path: str, min_chars: int, max_garbled_ratio: float) -> dict[str, Any]:
    """兼容封装：收集逐页结果（单文档调用方使用；批处理走 parse_stage 的流式路径）。"""
    pages = list(_iter_parsed_pages(path, min_chars, max_garbled_ratio))
    return {"page_count": len(pages), "pages": pages}


def _extract_tables_markdown(page) -> str:
    """用 PyMuPDF find_tables 把页面表格恢复为 Markdown 表格。

    D8：get_text 只取纯文本会把表格行列压平；这里保留列结构，
    供下游（LLM/规则/人工）按列归属读取财务数据。
    """
    try:
        tables = page.find_tables()
    except Exception:  # noqa: BLE001 - 表格检测失败不应中断解析
        return ""
    if not tables.tables:
        return ""
    parts: list[str] = []
    for table in tables.tables:
        data = table.extract()
        if not data:
            continue
        rows: list[list[str]] = []
        for row in data:
            rows.append([
                str(cell).replace("\r", " ").replace("\n", " ").strip()
                if cell is not None else ""
                for cell in row
            ])
        if not rows:
            continue
        width = max(len(row) for row in rows)
        header = rows[0]
        parts.append("| " + " | ".join(header) + " |")
        parts.append("|" + "|".join(["---"] * max(width, 1)) + "|")
        for row in rows[1:]:
            padded = row + [""] * (width - len(row))
            parts.append("| " + " | ".join(padded) + " |")
        parts.append("")
    return "\n".join(parts).strip()


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
        "SELECT doc_id, primary_path, attempt_count FROM documents "
        "WHERE status IN ('ingested','parse_failed') AND attempt_count < ? "
        "ORDER BY filename",
        (MAX_PARSE_ATTEMPTS,),
    )
    if limit is not None:
        rows = rows[:limit]
    summary = {"selected": len(rows), "parsed": 0, "failed": 0, "ocr_pages": 0}
    if not rows:
        return summary

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(_parse_and_store, config, db, row, min_chars, max_garbled): row
            for row in rows
        }
        for future in as_completed(futures):
            row = futures[future]
            doc_id = row["doc_id"]
            try:
                outcome = future.result()
                if "error" in outcome:
                    raise outcome["error"]
                summary["parsed"] += 1
                summary["ocr_pages"] += outcome["ocr_pages"]
                status = "parsed" if outcome["ocr_pages"] == 0 else "parsed_native"
                db.execute(
                    """
                    UPDATE documents SET page_count=?, status=?, text_page_count=?,
                        ocr_page_count=0, error=NULL, parser_version=?, updated_at=?
                    WHERE doc_id=?
                    """,
                    (
                        outcome["page_count"], status,
                        outcome["page_count"] - outcome["ocr_pages"], PARSER_VERSION, utcnow(), doc_id,
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - per-file isolation is intentional
                summary["failed"] += 1
                db.add_error(doc_id, "parse", exc)
                # D16：失败计数累加，超阈值置 parse_dead 排除（防永久损坏 PDF 无限重试）
                attempts = int(row.get("attempt_count") or 0)
                db.execute(
                    """
                    UPDATE documents SET status=?, error=?, attempt_count=attempt_count+1,
                        updated_at=? WHERE doc_id=?
                    """,
                    (
                        "parse_dead" if attempts + 1 >= MAX_PARSE_ATTEMPTS else "parse_failed",
                        str(exc)[:4000], utcnow(), doc_id,
                    ),
                )
    return summary


def _parse_and_store(config: ProjectConfig, db: Database, row, min_chars: int, max_garbled: float) -> dict[str, int]:
    """D36：流式解析并分批写库——逐页 yield，每 500 页一批 executemany，内存峰值受控。"""
    doc_id = row["doc_id"]
    try:
        now = utcnow()
        page_count = 0
        ocr_pages = 0
        with db.transaction() as conn:
            conn.execute("DELETE FROM pages WHERE doc_id=?", (doc_id,))
            batch: list[tuple] = []
            for page in _iter_parsed_pages(row["primary_path"], min_chars, max_garbled):
                batch.append((
                    doc_id, page["page_no"], page["width"], page["height"],
                    page["native_text"], page["final_text"], page["parse_method"],
                    page["printable_chars"], page["garbled_ratio"],
                    page["needs_ocr"], page["ocr_status"], now,
                ))
                page_count += 1
                ocr_pages += page["needs_ocr"]
                if len(batch) >= 500:
                    conn.executemany(
                        """
                        INSERT INTO pages(
                            doc_id, page_no, width, height, native_text, final_text,
                            parse_method, printable_chars, garbled_ratio, needs_ocr,
                            ocr_status, updated_at
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        batch,
                    )
                    batch.clear()
            if batch:
                conn.executemany(
                    """
                    INSERT INTO pages(
                        doc_id, page_no, width, height, native_text, final_text,
                        parse_method, printable_chars, garbled_ratio, needs_ocr,
                        ocr_status, updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    batch,
                )
        return {"page_count": page_count, "ocr_pages": ocr_pages}
    except Exception as exc:  # noqa: BLE001 - 由调用方按文档隔离处理
        return {"error": exc}


def render_page(path: str, page_no: int, dpi: int = 220, document: Any | None = None) -> bytes:
    """渲染指定页为 PNG。D35：传入已打开的 Document 时复用句柄，避免逐页重复打开 PDF。"""
    if document is not None:
        page = document.load_page(page_no - 1)
        pixmap = page.get_pixmap(dpi=dpi, alpha=False)
        return pixmap.tobytes("png")
    with fitz.open(path) as document:
        page = document.load_page(page_no - 1)
        pixmap = page.get_pixmap(dpi=dpi, alpha=False)
        return pixmap.tobytes("png")
