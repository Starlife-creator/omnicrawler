"""供独立模块和统一工作台调用的稳定服务 API。"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import ProjectConfig, load_config, validate_runtime_config
from .database import Database
from .exporter import export_stage
from .extraction import extraction_stage
from .ingest import ingest
from .ocr import ocr_stage
from .parser import parse_stage
from .text_export import export_text_stage

LOGGER = logging.getLogger(__name__)

EventCallback = Callable[[str, dict[str, Any]], None]
StopCallback = Callable[[], bool]


def database_status(db: Database) -> dict[str, Any]:
    documents = {
        row["status"]: row["n"]
        for row in db.fetchall("SELECT status, COUNT(*) AS n FROM documents GROUP BY status")
    }
    pages = db.fetchone(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(needs_ocr),0) AS need_ocr,
                  COALESCE(SUM(CASE WHEN ocr_status='done' THEN 1 ELSE 0 END),0) AS ocr_done,
                  COALESCE(SUM(is_candidate),0) AS candidates
           FROM pages"""
    )
    records = db.fetchone(
        """SELECT COUNT(*) AS total,
                  COALESCE(SUM(CASE WHEN review_status='needs_review' THEN 1 ELSE 0 END),0) AS needs_review,
                  COALESCE(SUM(CASE WHEN validation_status='invalid' THEN 1 ELSE 0 END),0) AS invalid
           FROM records"""
    )
    errors = db.fetchone("SELECT COUNT(*) AS n FROM errors")["n"]
    return {
        "documents": documents,
        "pages": dict(pages) if pages else {},
        "records": dict(records) if records else {},
        "errors": errors,
    }


def _emit(callback: EventCallback | None, stage: str, result: dict[str, Any]) -> None:
    if callback:
        callback(stage, result)


def _stopped(should_stop: StopCallback | None) -> bool:
    return bool(should_stop and should_stop())


def prepare_config(config_path: str | Path) -> ProjectConfig:
    config = load_config(config_path)
    validate_runtime_config(config)
    config.work_dir.mkdir(parents=True, exist_ok=True)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    return config


def run_processing(
    config_path: str | Path,
    *,
    limit: int | None = None,
    workers: int | None = None,
    ocr_workers: int | None = None,
    run_ocr: bool = True,
    callback: EventCallback | None = None,
    should_stop: StopCallback | None = None,
) -> dict[str, Any]:
    """Ingest any local PDFs, parse pages, optionally OCR, and export text."""
    config = prepare_config(config_path)
    warnings = validate_runtime_config(config)
    _emit(callback, "warnings", {"items": warnings})
    results: dict[str, Any] = {"warnings": warnings}
    with Database(config.database) as db:
        for stage, operation in (
            ("ingest", lambda: ingest(config, db, limit)),
            ("parse", lambda: parse_stage(config, db, limit, workers)),
        ):
            if _stopped(should_stop):
                results["stopped"] = True
                return results
            _emit(callback, f"{stage}_started", {})
            # B1：阶段级异常隔离——单阶段失败保留已完成结果与失败清单，不整批 failed
            try:
                result = operation()
            except Exception as exc:  # noqa: BLE001 - stage isolation keeps partial results
                LOGGER.exception("PDF 管线阶段 %s 失败", stage)
                results[stage] = {"failed": True, "error": str(exc)}
                _emit(callback, stage, {"failed": True, "error": str(exc)})
                results["stopped"] = True
                return results
            results[stage] = result
            _emit(callback, stage, result)
        if run_ocr and not _stopped(should_stop):
            _emit(callback, "ocr_started", {})
            # D39：透传 ocr_workers，GUI/CLI 并行 OCR 才真正生效
            result = ocr_stage(config, db, ocr_workers=ocr_workers or 1)
            results["ocr"] = result
            _emit(callback, "ocr", result)
        if _stopped(should_stop):
            results["stopped"] = True
            return results
        _emit(callback, "text_export_started", {})
        result = export_text_stage(config, db, limit)
        results["text_export"] = result
        _emit(callback, "text_export", result)
        results["status"] = database_status(db)
        _emit(callback, "status", results["status"])
    return results


def run_extraction(
    config_path: str | Path,
    *,
    limit: int | None = None,
    workers: int | None = None,
    auto_prepare: bool = True,
    run_ocr: bool = True,
    callback: EventCallback | None = None,
    should_stop: StopCallback | None = None,
) -> dict[str, Any]:
    """Run extraction independently; missing ingest/parse stages can be prepared automatically."""
    results: dict[str, Any] = {}
    if auto_prepare:
        results["processing"] = run_processing(
            config_path,
            limit=limit,
            workers=workers,
            run_ocr=run_ocr,
            callback=callback,
            should_stop=should_stop,
        )
        if results["processing"].get("stopped"):
            return results
    if _stopped(should_stop):
        results["stopped"] = True
        return results
    config = prepare_config(config_path)
    if not auto_prepare:
        warnings = validate_runtime_config(config)
        results["warnings"] = warnings
        _emit(callback, "warnings", {"items": warnings})
    with Database(config.database) as db:
        _emit(callback, "extract_started", {})
        # B1：抽取/导出阶段异常隔离
        try:
            result = extraction_stage(config, db, limit, workers, should_stop=should_stop)
        except Exception as exc:  # noqa: BLE001 - stage isolation keeps partial results
            LOGGER.exception("PDF 抽取阶段失败")
            results["extract"] = {"failed": True, "error": str(exc)}
            _emit(callback, "extract", {"failed": True, "error": str(exc)})
            results["stopped"] = True
            return results
        results["extract"] = result
        _emit(callback, "extract", result)
        if _stopped(should_stop):
            results["stopped"] = True
            return results
        _emit(callback, "export_started", {})
        try:
            result = export_stage(config, db)
        except Exception as exc:  # noqa: BLE001 - export failure keeps extraction results
            LOGGER.exception("PDF 导出阶段失败")
            results["export"] = {"failed": True, "error": str(exc)}
            _emit(callback, "export", {"failed": True, "error": str(exc)})
            results["stopped"] = True
            return results
        results["export"] = result
        _emit(callback, "export", result)
        results["status"] = database_status(db)
        _emit(callback, "status", results["status"])
    return results
