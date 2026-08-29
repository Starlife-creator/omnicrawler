from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypedDict

import pypdfium2 as pdfium
from pdfminer.layout import LAParams

from .concurrency import iter_bounded_futures
from .config import ProjectConfig
from .database import Database
from .utils import clean_text, utcnow

PARSER_VERSION = "native-1.0"

# D16：同一文档解析失败最大次数，超过后置 parse_dead 排除
MAX_PARSE_ATTEMPTS = 3


class ParseOutcome(TypedDict, total=False):
    page_count: int
    ocr_pages: int
    error: Exception

# Phase 0（M0a）：pdfplumber 底层 pdfminer 的布局参数——sort 语义对齐原
# fitz get_text(sort=True)（阅读顺序），laparams 控制词/行合并容差。
_TEXT_LAPARAMS = LAParams(line_margin=0.3, word_margin=0.1, char_margin=2.0, boxes_flow=0.5)


def text_quality(text: str) -> tuple[int, float]:
    printable = sum(1 for char in text if char.isprintable() and not char.isspace())
    if not text:
        return 0, 1.0
    bad = text.count("\ufffd") + text.count("\x00")
    control = sum(1 for char in text if ord(char) < 32 and char not in "\n\r\t")
    ratio = min(1.0, (bad + control) / max(1, len(text)))
    return printable, ratio


def _image_coverage_ratio(page) -> float:
    """页面图片总面积占比（D12：纯图表格页夹带页眉页脚时强制 OCR）。

    Phase 0：pdfplumber page.images（bbox 为 PDF 点坐标，与页面同坐标系）。
    """
    try:
        images = page.images
        if not images:
            return 0.0
        page_area = max(float(page.width) * float(page.height), 1.0)
        covered = 0.0
        for image in images:
            width = float(image.get("x1", 0.0)) - float(image.get("x0", 0.0))
            height = float(image.get("bottom", 0.0)) - float(image.get("top", 0.0))
            covered += max(width, 0.0) * max(height, 0.0)
        return min(covered / page_area, 1.0)
    except Exception:  # noqa: BLE001 - 判据失败按无图处理
        return 0.0


def open_document(path: str):
    """Phase 0（M0a）：打开 PDF 文档句柄（供渲染句柄复用，D35 语义保留）。

    返回 pypdfium2.PdfDocument；调用方负责 close（或用 with 语义）。
    加密检测前置：pypdfium2 打开加密文档渲染会失败，先显式拒绝保持原
    PermissionError("PDF需要密码") 行为。
    """
    from pypdf import PdfReader

    if PdfReader(path).is_encrypted:
        raise PermissionError("PDF需要密码")
    return pdfium.PdfDocument(path)


def _iter_parsed_pages(path: str, min_chars: int, max_garbled_ratio: float):
    """D36：逐页 yield 解析结果，避免整文档 pages 列表常驻内存（数千页大文件内存峰值受控）。

    Phase 0（M0a）：fitz → pdfplumber（文本/表格/图像）+ pypdf 前置加密检测。
    """
    import pdfplumber
    from pypdf import PdfReader

    reader = PdfReader(path)
    if reader.is_encrypted:
        raise PermissionError("PDF需要密码")
    with pdfplumber.open(path) as document:
        for page_index, page in enumerate(document.pages):
            raw_text = page.extract_text(layout=False, laparams=_TEXT_LAPARAMS) or ""
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
            yield {
                "page_no": page_index + 1,
                "width": float(page.width),
                "height": float(page.height),
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
    """用 pdfplumber find_tables 把页面表格恢复为 Markdown 表格。

    D8：纯文本提取会把表格行列压平；这里保留列结构，
    供下游（LLM/规则/人工）按列归属读取财务数据。
    Phase 0：fitz.find_tables → pdfplumber.find_tables（API 同构：extract() 返回行列二维数组）。
    """
    try:
        tables = page.find_tables()
    except Exception:  # noqa: BLE001 - 表格检测失败不应中断解析
        return ""
    if not tables:
        return ""
    parts: list[str] = []
    for table in tables:
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
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    if limit is not None and limit < 0:
        raise ValueError("limit 不能为负数")
    parser_config = config.parser
    min_chars = int(parser_config.get("min_native_chars", 40))
    max_garbled = float(parser_config.get("max_garbled_ratio", 0.03))
    workers = int(parser_config.get("workers", 4)) if workers is None else workers
    if not 1 <= workers <= 64:
        raise ValueError("workers 必须在1到64之间")
    select_sql = (
        "SELECT doc_id, primary_path, attempt_count FROM documents "
        "WHERE status IN ('ingested','parse_failed') AND attempt_count < ? "
        "ORDER BY filename, doc_id"
    )
    select_params: tuple[Any, ...] = (MAX_PARSE_ATTEMPTS,)
    if hasattr(db, "iter_rows"):
        total_row = db.fetchone(
            "SELECT COUNT(*) AS n FROM documents "
            "WHERE status IN ('ingested','parse_failed') AND attempt_count < ?",
            (MAX_PARSE_ATTEMPTS,),
        )
        selected = int(total_row["n"] if total_row else 0)
        if limit is not None:
            selected = min(selected, limit)
            select_sql += " LIMIT ?"
            select_params += (limit,)
        rows = db.iter_rows(select_sql, select_params)
    else:  # Lightweight test doubles and third-party Database adapters.
        buffered_rows = db.fetchall(select_sql, select_params)
        if limit is not None:
            buffered_rows = buffered_rows[:limit]
        selected = len(buffered_rows)
        rows = iter(buffered_rows)
    summary: dict[str, Any] = {
        "selected": selected, "parsed": 0, "failed": 0, "ocr_pages": 0,
    }
    if not selected:
        return summary

    # S1.5.1：每线程复用独立数据库连接（参照 extraction.py 每线程 DB 模式），
    # 避免多线程共享单连接并发写导致 "cannot start a transaction within a transaction" / SQLITE_BUSY
    _thread_db = threading.local()
    _thread_connections: list[Database] = []

    def work(row):
        worker_db = getattr(_thread_db, "db", None)
        if worker_db is None:
            worker_db = Database(config.database)
            _thread_db.db = worker_db
            _thread_connections.append(worker_db)  # GIL 下 list.append 线程安全
        return _parse_and_store(config, worker_db, row, min_chars, max_garbled)

    def mark_stopped() -> None:
        summary["stopped"] = True

    try:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            completed = iter_bounded_futures(
                rows,
                lambda row: pool.submit(work, row),
                max_in_flight=max(1, workers * 4),
                should_stop=should_stop,
                on_stop=mark_stopped,
            )
            for future, row in completed:
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
                    attempts = int(row["attempt_count"] or 0)
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
    finally:
        for conn in _thread_connections:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - 线程连接关闭失败不应影响调用方
                pass
    return summary


def _parse_and_store(
    config: ProjectConfig,
    db: Database,
    row,
    min_chars: int,
    max_garbled: float,
) -> ParseOutcome:
    """先流式解析到内存页列表，再开单事务 executemany（S1.5.1 短事务批写）。

    长事务覆盖整个解析会与其他线程互相阻塞；改为把解析（纯内存，无锁）与
    写库（单次 BEGIN IMMEDIATE 短事务）分离，避免 SQLITE_BUSY / 嵌套事务。
    """
    doc_id = row["doc_id"]
    try:
        now = utcnow()
        page_count = 0
        ocr_pages = 0
        pages: list[tuple] = []
        for page in _iter_parsed_pages(row["primary_path"], min_chars, max_garbled):
            pages.append((
                doc_id, page["page_no"], page["width"], page["height"],
                page["native_text"], page["final_text"], page["parse_method"],
                page["printable_chars"], page["garbled_ratio"],
                page["needs_ocr"], page["ocr_status"], now,
            ))
            page_count += 1
            ocr_pages += page["needs_ocr"]
        with db.transaction() as conn:
            conn.execute("DELETE FROM pages WHERE doc_id=?", (doc_id,))
            batch: list[tuple] = []
            for item in pages:
                batch.append(item)
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


def _render_to_png(page, dpi: int) -> bytes:
    """Phase 0（M0a）：pypdfium2 单页渲染为 PNG 字节（BGRA→PIL，方案优化点）。"""
    import io

    bitmap = page.render(scale=dpi / 72)
    pil = bitmap.to_pil()
    buffer = io.BytesIO()
    pil.save(buffer, format="PNG")
    return buffer.getvalue()


def render_page(path: str, page_no: int, dpi: int = 220, document: Any | None = None) -> bytes:
    """渲染指定页为 PNG。D35：传入已打开的 Document 时复用句柄，避免逐页重复打开 PDF。

    Phase 0（M0a）：fitz get_pixmap → pypdfium2 render（scale=dpi/72，语义等价）。
    """
    if document is not None:
        page = document[page_no - 1]
        return _render_to_png(page, dpi)
    with pdfium.PdfDocument(path) as document:
        page = document[page_no - 1]
        return _render_to_png(page, dpi)
