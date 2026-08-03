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
        confidence = statistics.fmean(scores) if scores else None
        return clean_text("\n".join(markdown_parts)), confidence


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
        self.lang = config.get("lang", "chi_sim+eng")
        command = str(config.get("command") or os.environ.get("TESSERACT_CMD", "")).strip()
        if command:
            self.pytesseract.pytesseract.tesseract_cmd = command

    def recognize(self, png_bytes: bytes) -> tuple[str, float | None]:
        image = self.Image.open(io.BytesIO(png_bytes)).convert("RGB")
        data = self.pytesseract.image_to_data(
            image, lang=self.lang, output_type=self.pytesseract.Output.DICT
        )
        words: list[str] = []
        scores: list[float] = []
        for text, confidence in zip(data.get("text", []), data.get("conf", []), strict=False):
            if str(text).strip():
                words.append(str(text))
                try:
                    score = float(confidence)
                    if score >= 0:
                        scores.append(score / 100)
                except (TypeError, ValueError):
                    pass
        return clean_text(" ".join(words)), statistics.fmean(scores) if scores else None


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


def _ocr_worker_process(args: tuple[str, int, int]) -> tuple[str, int, str | None, float | None, int, float]:
    """单个 worker 进程的处理函数：渲染 + OCR 识别。

    Returns:
        (doc_id, page_no, text, confidence, printable_chars, garbled_ratio)
        若失败则 text 为 None。
    """
    global _worker_backend
    primary_path, page_no, dpi = args
    doc_id = os.path.basename(primary_path)  # 近似 — 实际 doc_id 由调用方提供
    try:
        if _worker_backend is None:
            raise RuntimeError("OCR backend 未初始化")

        png = render_page(primary_path, page_no, dpi=dpi)
        text, confidence = _worker_backend.recognize(png)
        printable, garbled = text_quality(text)
        return (doc_id, page_no, text, confidence, printable, garbled)
    except Exception:
        return (doc_id, page_no, None, None, 0, 0.0)


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
    backend = create_backend(config)
    if backend is None:
        summary["skipped"] = len(rows)
        return summary

    dpi = int(config.ocr.get("dpi", 220))
    workers = adaptive_ocr_workers(ocr_workers)

    if workers <= 1:
        # 串行路径（行为不变）
        return _ocr_serial(config, db, rows, backend, dpi, summary)

    # 多进程路径
    logger.info("OCR 阶段启动 %d 个 worker 进程", workers)
    work_items: list[tuple[str, int, int, str, int]] = []
    for row in rows:
        doc_id = row["doc_id"]
        page_no = int(row["page_no"])
        path = row["primary_path"]
        work_items.append((doc_id, page_no, path, dpi))

    ocr_config = dict(config.ocr)
    start_time = time.monotonic()

    with concurrent.futures.ProcessPoolExecutor(
        max_workers=workers,
        initializer=_ocr_worker_init,
        initargs=(ocr_config,),
    ) as executor:
        futures: dict[concurrent.futures.Future, tuple[str, int]] = {}
        for doc_id, page_no, path, _dpi in work_items:
            fut = executor.submit(_ocr_worker_process, (path, page_no, _dpi))
            futures[fut] = (doc_id, page_no)

        completed = 0
        for fut in concurrent.futures.as_completed(futures):
            doc_id, page_no = futures[fut]
            try:
                _, _, text, confidence, printable, garbled = fut.result()
            except Exception as exc:  # noqa: BLE001
                db.add_error(doc_id, "ocr", exc)
                db.execute(
                    "UPDATE pages SET ocr_status='failed', updated_at=? WHERE doc_id=? AND page_no=?",
                    (utcnow(), doc_id, page_no),
                )
                summary["failed"] += 1
                continue

            if text is None:
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
            # 温度保护：每 10 页检查一次
            if completed % 10 == 0 and not _check_temperature():
                logger.warning("温度过高，等待 30 秒后继续...")
                time.sleep(30)

    elapsed = time.monotonic() - start_time
    logger.info(
        "OCR 阶段完成：%d/%d 页识别成功，%d 失败，耗时 %.1f 秒（%d workers）",
        summary["recognized"], summary["selected"], summary["failed"], elapsed, workers,
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
    """串行 OCR 路径 — 与 2.7.0 行为一致。"""
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
