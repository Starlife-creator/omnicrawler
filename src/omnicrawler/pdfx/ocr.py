from __future__ import annotations

import concurrent.futures
import io
import logging
import os
import statistics
import time
from typing import Any, Protocol

from .config import ProjectConfig
from .database import Database
from .parser import render_page, text_quality
from .utils import clean_text, utcnow

logger = logging.getLogger(__name__)

# 温度监控开关 — 默认 85°C，超阈值自动降 worker
_OCR_MAX_TEMP = float(os.environ.get("OMNICRAWL_OCR_MAX_TEMP", "85"))


class OCRBackend(Protocol):
    def recognize(self, png_bytes: bytes) -> tuple[str, float | None]: ...


class PaddleStructureBackend:
    def __init__(self, config: dict[str, Any]):
        try:
            import numpy as np
            from paddleocr import PPStructureV3
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError(
                "缺少PaddleOCR依赖，请运行 install_all.py --ocr paddle，"
                "或安装 pdf-data-core[ocr-paddle]"
            ) from exc
        self.np = np
        self.Image = Image
        self.pipeline = PPStructureV3(
            lang=config.get("lang", "ch"),
            device=config.get("device", "cpu"),
            use_doc_orientation_classify=bool(config.get("orientation", True)),
            use_doc_unwarping=bool(config.get("unwarping", False)),
            use_textline_orientation=bool(config.get("textline_orientation", True)),
            use_table_recognition=bool(config.get("table_recognition", True)),
            use_formula_recognition=bool(config.get("formula_recognition", False)),
            use_chart_recognition=False,
        )

    def recognize(self, png_bytes: bytes) -> tuple[str, float | None]:
        image = self.Image.open(io.BytesIO(png_bytes)).convert("RGB")
        array = self.np.asarray(image)
        output = list(self.pipeline.predict(array))
        markdown_parts: list[str] = []
        scores: list[float] = []
        for result in output:
            markdown = getattr(result, "markdown", {}) or {}
            markdown_parts.append(str(markdown.get("markdown_texts", "")))
            result_json = getattr(result, "json", {}) or {}
            overall = result_json.get("overall_ocr_res", {}) if isinstance(result_json, dict) else {}
            for score in overall.get("rec_scores", []) or []:
                try:
                    scores.append(float(score))
                except (TypeError, ValueError):
                    pass
            # D10：PPStructureV3 的表格 HTML 在 json.res（type=table）中，未进入 markdown_texts 时显式提取
            res_list = result_json.get("res", []) if isinstance(result_json, dict) else []
            table_htmls = []
            for region in res_list:
                if not isinstance(region, dict) or str(region.get("type", "")).casefold() != "table":
                    continue
                html = region.get("res", {})
                if isinstance(html, dict):
                    html = html.get("html", "")
                if isinstance(html, str) and html.strip():
                    table_htmls.append(html)
            if table_htmls:
                markdown_parts.append(_table_html_to_markdown(table_htmls))
        confidence = statistics.fmean(scores) if scores else None
        return clean_text("\n".join(markdown_parts)), confidence


def _table_html_to_markdown(html_parts: list[str]) -> str:
    """把表格 HTML（<table><tr><td>）转为 Markdown 表格（D10：表格结构不进最终文本时补充）。"""
    import re as html_re

    lines: list[str] = []
    for html in html_parts:
        rows = html_re.findall(r"<tr[^>]*>(.*?)</tr>", html, html_re.S | html_re.I)
        if not rows:
            continue
        markdown_rows: list[list[str]] = []
        for row_html in rows:
            cells = html_re.findall(
                r"<(?:td|th)[^>]*>(.*?)</(?:td|th)>", row_html, html_re.S | html_re.I
            )
            markdown_rows.append([
                html_re.sub(r"<[^>]+>", "", cell).replace("\n", " ").strip()
                for cell in cells
            ])
        if not markdown_rows:
            continue
        width = max(len(row) for row in markdown_rows)
        header = markdown_rows[0]
        lines.append("| " + " | ".join(header) + " |")
        lines.append("|" + "|".join(["---"] * max(width, 1)) + "|")
        for row in markdown_rows[1:]:
            padded = row + [""] * (width - len(row))
            lines.append("| " + " | ".join(padded) + " |")
        lines.append("")
    return "\n".join(lines).strip()


# S2.3.3：非标准 Tesseract 语言名归一（ch→chi_sim 等），避免配置 lang 不兼容静默 OCR 失败
_LANG_ALIASES = {
    "ch": "chi_sim",
    "chi": "chi_sim",
    "cn": "chi_sim",
    "sim": "chi_sim",
    "trad": "chi_tra",
    "jp": "jpn",
    "jap": "jpn",
    "kr": "kor",
    "kor": "kor",
}


def normalize_ocr_lang(lang: str) -> str:
    """把 ``+`` 分隔的 Tesseract 语言串归一为标准语言名（未知项原样保留）。"""
    parts = [part.strip() for part in str(lang).split("+") if part.strip()]
    if not parts:
        return "chi_sim+eng"
    return "+".join(_LANG_ALIASES.get(part.casefold(), part) for part in parts)


class TesseractBackend:
    def __init__(self, config: dict[str, Any]):
        try:
            import pytesseract
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError(
                "缺少Tesseract依赖，请运行 install_all.py --ocr tesseract，"
                "并按操作系统安装 Tesseract 程序和中文语言包"
            ) from exc
        self.pytesseract = pytesseract
        self.Image = Image
        self.lang = normalize_ocr_lang(config.get("lang", "chi_sim+eng"))
        command = str(config.get("command") or os.environ.get("TESSERACT_CMD", "")).strip()
        if command:
            self.pytesseract.pytesseract.tesseract_cmd = command

    def recognize(self, png_bytes: bytes) -> tuple[str, float | None]:
        image = self.Image.open(io.BytesIO(png_bytes)).convert("RGB")
        data = self.pytesseract.image_to_data(
            image, lang=self.lang, output_type=self.pytesseract.Output.DICT
        )
        # D9：按 (block, par, line) 分行、left 分列重建，扫描件表格不再拍平为一行
        lines: dict[tuple[int, int, int], list[tuple[float, float, str]]] = {}
        scores: list[float] = []
        text_list = data.get("text", [])
        for index, text in enumerate(text_list):
            word = str(text).strip()
            if not word:
                continue
            key = (
                int(data["block_num"][index]),
                int(data["par_num"][index]),
                int(data["line_num"][index]),
            )
            lines.setdefault(key, []).append((
                float(data["left"][index]),
                float(data["width"][index]),
                word,
            ))
            try:
                conf = float(data["conf"][index])
                if conf >= 0:
                    scores.append(conf / 100)
            except (TypeError, ValueError):
                pass
        ordered = sorted(lines.items(), key=lambda item: (item[0][0], item[0][1], item[0][2]))
        text_lines: list[str] = []
        for _key, words in ordered:
            words.sort(key=lambda item: item[0])  # 行内按 left 排序
            parts: list[str] = []
            prev_right: float | None = None
            for left, width, word in words:
                if prev_right is None:
                    parts.append(word)
                else:
                    gap = left - prev_right
                    # 列间隙大 → 多空格分隔（保留列对齐信号）
                    if gap < 4:
                        parts.append(" " + word)
                    else:
                        parts.append(" " * min(6, max(2, int(gap / 8))) + word)
                prev_right = left + width
            text_lines.append("".join(parts))
        text = "\n".join(text_lines)
        return clean_text(text, compress_ws=False), statistics.fmean(scores) if scores else None


def create_backend(config: ProjectConfig) -> OCRBackend | None:
    name = str(config.ocr.get("backend", "none")).lower()
    if name == "none":
        return None
    if name == "paddle":
        return PaddleStructureBackend(config.ocr)
    if name == "tesseract":
        return TesseractBackend(config.ocr)
    raise ValueError(f"不支持的OCR后端: {name}")


def adaptive_ocr_workers(requested: int) -> int:
    """自适应 OCR worker 数：基于 CPU 核数和可用内存自动推荐保守值。

    规则：
    - 默认推荐不超过物理核数的一半
    - 8GB 可用→2、16GB→4、24GB→6
    - 上限 min(cpu_count, available_memory_gb / 2.5)
    """
    if requested <= 0:
        return 1
    try:
        import os as _os
        cpu = _os.cpu_count() or 4
    except Exception:
        cpu = 4
    try:
        import psutil
        mem = psutil.virtual_memory()
        avail_gb = mem.available / (1024 ** 3)
    except Exception:
        avail_gb = 4  # 保守默认

    # 推荐值不超过物理核数的一半（给其他进程留空间）
    recommended = min(requested, cpu // 2)
    # 内存约束：每个 worker 约需 2.5 GB
    mem_limit = max(1, int(avail_gb / 2.5))
    return max(1, min(recommended, mem_limit, cpu))


def _check_temperature() -> bool:
    """检查 CPU 温度是否超过阈值。返回 True 表示温度正常。"""
    try:
        import psutil
        temps = psutil.sensors_temperatures()
        if not temps:
            return True
        for name, entries in temps.items():
            for entry in entries:
                if entry.current and entry.current > _OCR_MAX_TEMP:
                    logger.warning(
                        "CPU 温度 %.1f°C 超过阈值 %.0f°C（传感器: %s），暂停新增 worker",
                        entry.current, _OCR_MAX_TEMP, name,
                    )
                    return False
    except Exception:
        pass
    return True


def _ocr_worker_init(ocr_config: dict[str, Any]) -> None:
    """进程池初始化：为当前 worker 进程创建 OCR backend 实例。"""
    global _worker_backend
    name = str(ocr_config.get("backend", "none")).lower()
    if name == "paddle":
        _worker_backend = PaddleStructureBackend(ocr_config)
    elif name == "tesseract":
        _worker_backend = TesseractBackend(ocr_config)
    else:
        _worker_backend = None


_worker_backend: OCRBackend | None = None
# D35：worker 内复用 PDF 句柄（同文档多页不重复打开）
_worker_document: Any | None = None
_worker_document_path: str | None = None


def _ocr_worker_process(args: tuple[str, int, int]) -> tuple[str, int, str | None, float | None, int, float]:
    """单个 worker 进程的处理函数：渲染 + OCR 识别。

    Returns:
        (doc_id, page_no, text, confidence, printable_chars, garbled_ratio)
        若失败则 text 为 None。
    """
    global _worker_backend, _worker_document, _worker_document_path
    primary_path, page_no, dpi = args
    doc_id = os.path.basename(primary_path)  # 近似 — 实际 doc_id 由调用方提供
    try:
        if _worker_backend is None:
            raise RuntimeError("OCR backend 未初始化")

        # D35：worker 内复用已打开的 PDF 句柄（同文档多页不再逐页打开）
        # Phase 0（M0a）：fitz.open → parser.open_document（pypdfium2 句柄）
        if _worker_document_path != primary_path:
            if _worker_document is not None:
                try:
                    _worker_document.close()
                except Exception:  # noqa: BLE001 - 句柄关闭失败不影响后续流程
                    pass
            _worker_document = None
        if _worker_document is None:
            from .parser import open_document

            _worker_document = open_document(primary_path)
            _worker_document_path = primary_path
        png = render_page(primary_path, page_no, dpi=dpi, document=_worker_document)
        text, confidence = _worker_backend.recognize(png)
        printable, garbled = text_quality(text)
        return (doc_id, page_no, text, confidence, printable, garbled, None)
    except Exception as exc:  # noqa: BLE001
        # D15：worker 把错误信息带回主进程，主进程写 errors 表而非仅标 failed
        return (doc_id, page_no, None, None, 0, 0.0, str(exc))


def ocr_stage(
    config: ProjectConfig,
    db: Database,
    limit_pages: int | None = None,
    *,
    ocr_workers: int = 1,
) -> dict[str, int]:
    """OCR 阶段：对缺少文字层的页面执行 OCR 识别。

    Args:
        config: 项目配置
        db: 数据库连接
        limit_pages: 限制处理页数（用于小规模试跑）
        ocr_workers: OCR 并行 worker 数（默认 1 即串行；>1 使用多进程并行加速）
    """
    if limit_pages is not None and limit_pages < 0:
        raise ValueError("limit_pages 不能为负数")
    rows = db.fetchall(
        """
        SELECT p.doc_id, p.page_no, d.primary_path
        FROM pages p JOIN documents d ON d.doc_id=p.doc_id
        WHERE p.needs_ocr=1 AND p.ocr_status IN ('pending','failed')
        ORDER BY p.doc_id, p.page_no
        """
    )
    if limit_pages is not None:
        rows = rows[:limit_pages]
    summary: dict[str, int] = {"selected": len(rows), "recognized": 0, "failed": 0, "skipped": 0}
    if not rows:
        return summary
    dpi = int(config.ocr.get("dpi", 220))
    workers = adaptive_ocr_workers(ocr_workers)

    if workers <= 1:
        # 串行路径：父进程创建 backend（失败按 D13 降级跳过，不中断整批）
        try:
            backend = create_backend(config)
        except Exception as exc:  # noqa: BLE001 - missing optional OCR dependency must degrade
            logger.error("OCR backend 初始化失败，跳过 OCR 阶段: %s", exc)
            for row in rows:
                db.add_error(row["doc_id"], "ocr", exc)
                db.execute(
                    "UPDATE pages SET ocr_status='failed', updated_at=? WHERE doc_id=? AND page_no=?",
                    (utcnow(), row["doc_id"], row["page_no"]),
                )
            summary["skipped"] = len(rows)
            return summary
        if backend is None:
            summary["skipped"] = len(rows)
            return summary
        return _ocr_serial(config, db, rows, backend, dpi, summary)

    # 多进程路径：D39 父进程不保留 backend 实例（PPStructureV3 占 1-2GB），
    # 但 S2.3.1 要求进入进程池前在本进程预检一次依赖（缺依赖/GPU 不可用早失败，
    # 提示"依赖缺失"并按 D13 语义标记 skipped 写 errors，不崩管线）。
    precheck_backend: OCRBackend | None = None
    try:
        precheck_backend = create_backend(config)
    except Exception as exc:  # noqa: BLE001 - missing optional OCR dependency must degrade
        logger.error("OCR 依赖缺失，跳过 OCR 阶段: %s", exc)
        for row in rows:
            db.add_error(row["doc_id"], "ocr", exc)
            db.execute(
                "UPDATE pages SET ocr_status='failed', updated_at=? WHERE doc_id=? AND page_no=?",
                (utcnow(), row["doc_id"], row["page_no"]),
            )
        summary["skipped"] = len(rows)
        return summary
    if precheck_backend is None:
        summary["skipped"] = len(rows)
        return summary
    del precheck_backend  # 预检实例即刻释放，父进程不驻留模型

    logger.info("OCR 阶段启动 %d 个 worker 进程", workers)
    work_items: list[tuple[str, int, int, str, int]] = []
    for row in rows:
        doc_id = row["doc_id"]
        page_no = int(row["page_no"])
        path = row["primary_path"]
        work_items.append((doc_id, page_no, path, dpi))

    ocr_config = dict(config.ocr)
    start_time = time.monotonic()
    completed = 0

    try:
        with concurrent.futures.ProcessPoolExecutor(
            max_workers=workers,
            initializer=_ocr_worker_init,
            initargs=(ocr_config,),
        ) as executor:
            # D37：按批提交（每批 500），避免百万页 futures 常驻；批间结果即时落库
            batch_size = 500
            for batch_start in range(0, len(work_items), batch_size):
                batch = work_items[batch_start:batch_start + batch_size]
                futures: dict[concurrent.futures.Future, tuple[str, int]] = {}
                for doc_id, page_no, path, _dpi in batch:
                    fut = executor.submit(_ocr_worker_process, (path, page_no, _dpi))
                    futures[fut] = (doc_id, page_no)
                # D38：温度保护前移到提交侧——每批提交后检查，任务不再满载后才 sleep
                if not _check_temperature():
                    logger.warning("温度过高，等待 30 秒后处理下一批...")
                    time.sleep(30)
                for fut in concurrent.futures.as_completed(futures):
                    doc_id, page_no = futures[fut]
                    try:
                        results = fut.result()
                        text, confidence, printable, garbled = results[2], results[3], results[4], results[5]
                    except Exception as exc:  # noqa: BLE001
                        db.add_error(doc_id, "ocr", exc)
                        db.execute(
                            "UPDATE pages SET ocr_status='failed', updated_at=? WHERE doc_id=? AND page_no=?",
                            (utcnow(), doc_id, page_no),
                        )
                        summary["failed"] += 1
                        completed += 1
                        continue

                    if text is None:
                        # D15：错误详情写 errors 表，用户可知为何无文字
                        worker_error = results[6] if len(results) > 6 and results[6] else "OCR 识别无输出"
                        db.add_error(doc_id, "ocr", RuntimeError(str(worker_error)[:4000]))
                        db.execute(
                            "UPDATE pages SET ocr_status='failed', updated_at=? WHERE doc_id=? AND page_no=?",
                            (utcnow(), doc_id, page_no),
                        )
                        summary["failed"] += 1
                    else:
                        db.execute(
                            """
                            UPDATE pages SET ocr_text=?, final_text=?, parse_method='ocr',
                                printable_chars=?, garbled_ratio=?, ocr_status='done',
                                ocr_confidence=?, updated_at=? WHERE doc_id=? AND page_no=?
                            """,
                            (text, text, printable, garbled, confidence, utcnow(), doc_id, page_no),
                        )
                        summary["recognized"] += 1
                    completed += 1
    except Exception as exc:  # noqa: BLE001 - S2.3.1: worker 池崩溃（BrokenProcessPool 等）降级不崩管线
        logger.error("OCR 多进程池崩溃，剩余 %d 页标记跳过: %s", len(rows) - completed, exc)
        for row in rows[completed:]:
            db.add_error(row["doc_id"], "ocr", RuntimeError(f"OCR 多进程崩溃: {exc}"[:4000]))
            db.execute(
                "UPDATE pages SET ocr_status='failed', updated_at=? WHERE doc_id=? AND page_no=?",
                (utcnow(), row["doc_id"], row["page_no"]),
            )
            summary["skipped"] += 1

    elapsed = time.monotonic() - start_time
    logger.info(
        "OCR 阶段完成：%d/%d 页识别成功，%d 失败，%d 跳过，耗时 %.1f 秒（%d workers）",
        summary["recognized"], summary["selected"], summary["failed"], summary["skipped"], elapsed, workers,
    )

    # 更新文档状态
    affected_docs: set[str] = {row["doc_id"] for row in rows}
    for doc_id in affected_docs:
        pending = db.fetchone(
            "SELECT COUNT(*) AS n FROM pages WHERE doc_id=? AND needs_ocr=1 AND ocr_status!='done'",
            (doc_id,),
        )["n"]
        done = db.fetchone(
            "SELECT COUNT(*) AS n FROM pages WHERE doc_id=? AND ocr_status='done'",
            (doc_id,),
        )["n"]
        status = "parsed" if pending == 0 else "parsed_partial"
        db.execute(
            "UPDATE documents SET status=?, ocr_page_count=?, updated_at=? WHERE doc_id=?",
            (status, done, utcnow(), doc_id),
        )
    return summary


def _ocr_serial(
    config: ProjectConfig,
    db: Database,
    rows: list[dict[str, Any]],
    backend: OCRBackend,
    dpi: int,
    summary: dict[str, int],
) -> dict[str, int]:
    """串行 OCR 路径 — 单进程逐页识别（默认；workers<=1 时的确定性低内存路径）。"""
    affected_docs: set[str] = set()
    for row in rows:
        doc_id, page_no = row["doc_id"], int(row["page_no"])
        affected_docs.add(doc_id)
        try:
            png = render_page(row["primary_path"], page_no, dpi=dpi)
            text, confidence = backend.recognize(png)
            printable, garbled = text_quality(text)
            db.execute(
                """
                UPDATE pages SET ocr_text=?, final_text=?, parse_method='ocr',
                    printable_chars=?, garbled_ratio=?, ocr_status='done',
                    ocr_confidence=?, updated_at=? WHERE doc_id=? AND page_no=?
                """,
                (text, text, printable, garbled, confidence, utcnow(), doc_id, page_no),
            )
            summary["recognized"] += 1
        except Exception as exc:  # noqa: BLE001
            db.add_error(doc_id, "ocr", exc)
            db.execute(
                "UPDATE pages SET ocr_status='failed', updated_at=? WHERE doc_id=? AND page_no=?",
                (utcnow(), doc_id, page_no),
            )
            summary["failed"] += 1

    for doc_id in affected_docs:
        pending = db.fetchone(
            "SELECT COUNT(*) AS n FROM pages WHERE doc_id=? AND needs_ocr=1 AND ocr_status!='done'",
            (doc_id,),
        )["n"]
        done = db.fetchone(
            "SELECT COUNT(*) AS n FROM pages WHERE doc_id=? AND ocr_status='done'",
            (doc_id,),
        )["n"]
        status = "parsed" if pending == 0 else "parsed_partial"
        db.execute(
            "UPDATE documents SET status=?, ocr_page_count=?, updated_at=? WHERE doc_id=?",
            (status, done, utcnow(), doc_id),
        )
    return summary
