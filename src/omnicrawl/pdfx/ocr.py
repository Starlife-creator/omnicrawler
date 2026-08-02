from __future__ import annotations

import io
import os
import statistics
from typing import Any, Protocol

from .config import ProjectConfig
from .database import Database
from .parser import render_page, text_quality
from .utils import clean_text, utcnow


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


def ocr_stage(config: ProjectConfig, db: Database, limit_pages: int | None = None) -> dict[str, int]:
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
    summary = {"selected": len(rows), "recognized": 0, "failed": 0, "skipped": 0}
    if not rows:
        return summary
    backend = create_backend(config)
    if backend is None:
        summary["skipped"] = len(rows)
        return summary

    dpi = int(config.ocr.get("dpi", 220))
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
