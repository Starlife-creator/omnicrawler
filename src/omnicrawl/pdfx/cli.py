from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from typing import Any

from .config import load_config, validate_runtime_config
from .database import Database
from .exporter import export_stage
from .extraction import extraction_stage
from .ingest import ingest
from .ocr import ocr_stage
from .parser import parse_stage
from .project import validate_project_template
from .review import apply_review
from .service import database_status
from .templates import DEFAULT_PDF_TEMPLATE
from .text_export import export_text_stage


def emit(stage: str, value: Any) -> None:
    print(json.dumps({"stage": stage, "result": value}, ensure_ascii=False, indent=2))


def doctor(config) -> dict[str, Any]:
    checks: dict[str, Any] = {
        "python": sys.version.split()[0],
        "config": str(config.path),
        "input_dir": {"path": str(config.input_dir), "exists": config.input_dir.exists()},
        "database": str(config.database),
        "output_dir": str(config.output_dir),
        "dependencies": {},
        "warnings": validate_runtime_config(config),
    }
    for module in ("fitz", "yaml", "openpyxl"):
        checks["dependencies"][module] = importlib.util.find_spec(module) is not None
    backend = str(config.ocr.get("backend", "none")).lower()
    if backend == "paddle":
        checks["dependencies"]["paddleocr"] = importlib.util.find_spec("paddleocr") is not None
        checks["dependencies"]["paddle"] = importlib.util.find_spec("paddle") is not None
    if backend == "tesseract":
        checks["dependencies"]["pytesseract"] = importlib.util.find_spec("pytesseract") is not None
        checks["dependencies"]["tesseract_program"] = shutil.which("tesseract") is not None
    return checks


def status(db: Database) -> dict[str, Any]:
    return database_status(db)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-core",
        description="十万级PDF结构化数据抽取流水线",
    )
    parser.add_argument("--config", default=DEFAULT_PDF_TEMPLATE, help="PDF 项目配置或内置模板引用")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="检查配置和运行环境")
    sub.add_parser("validate", help="只读校验配置、字段和正则")
    ingest_parser = sub.add_parser("ingest", help="扫描PDF、去重并建立文件清单")
    ingest_parser.add_argument("--limit", type=int)
    parse_parser = sub.add_parser("parse", help="解析PDF原生文字层")
    parse_parser.add_argument("--limit", type=int)
    parse_parser.add_argument("--workers", type=int)
    ocr_parser = sub.add_parser("ocr", help="只OCR缺少可用文字层的页面")
    ocr_parser.add_argument("--limit-pages", type=int)
    ocr_parser.add_argument("--ocr-workers", type=int, default=1,
                            help="OCR 并行 worker 数（默认 1，串行；增加可加速但占用更多内存）")
    extract_parser = sub.add_parser("extract", help="候选页召回、结构化抽取和校验")
    extract_parser.add_argument("--limit", type=int)
    extract_parser.add_argument("--workers", type=int)
    sub.add_parser("export", help="导出Excel和CSV")
    export_text_parser = sub.add_parser(
        "export-text", help="导出通用TXT、逐页JSONL和来源清单"
    )
    export_text_parser.add_argument("--limit", type=int)
    sub.add_parser("status", help="查看各阶段进度")
    reset_parser = sub.add_parser("reset", help="重置某一阶段及其下游结果")
    reset_parser.add_argument("stage", choices=["parse", "ocr", "extract"])
    review_parser = sub.add_parser("apply-review", help="导入人工复核后的Excel或CSV")
    review_parser.add_argument("--file", required=True, help="复核后的extraction_results.xlsx或review_queue.csv")
    run_parser = sub.add_parser("run", help="依次运行全部阶段")
    run_parser.add_argument("--limit", type=int, help="仅用于首次小规模试跑")
    run_parser.add_argument("--workers", type=int)
    run_parser.add_argument("--ocr-workers", type=int, default=1)
    run_parser.add_argument("--skip-ocr", action="store_true")
    process_parser = sub.add_parser(
        "process", help="独立运行PDF解析流程：扫描、解析、OCR、文本导出"
    )
    process_parser.add_argument("--limit", type=int)
    process_parser.add_argument("--workers", type=int)
    process_parser.add_argument("--ocr-workers", type=int, default=1)
    process_parser.add_argument("--skip-ocr", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    try:
        if args.command == "validate":
            result = validate_project_template(args.config)
            emit("validate", result)
            if not result["valid"]:
                raise SystemExit(2)
            return
        config = load_config(args.config)
        if args.command == "doctor":
            emit("doctor", doctor(config))
            return
        config.work_dir.mkdir(parents=True, exist_ok=True)
        config.output_dir.mkdir(parents=True, exist_ok=True)
        with Database(config.database) as db:
            if args.command == "ingest":
                emit("ingest", ingest(config, db, args.limit))
            elif args.command == "parse":
                emit("parse", parse_stage(config, db, args.limit, args.workers))
            elif args.command == "ocr":
                emit("ocr", ocr_stage(config, db, args.limit_pages, ocr_workers=args.ocr_workers))
            elif args.command == "extract":
                emit("extract", extraction_stage(config, db, args.limit, args.workers))
            elif args.command == "export":
                emit("export", export_stage(config, db))
            elif args.command == "export-text":
                emit("export-text", export_text_stage(config, db, args.limit))
            elif args.command == "status":
                emit("status", status(db))
            elif args.command == "reset":
                db.reset_stage(args.stage)
                emit("reset", {"stage": args.stage, "ok": True})
            elif args.command == "apply-review":
                emit("apply-review", apply_review(config, db, args.file))
            elif args.command == "run":
                warnings = validate_runtime_config(config)
                if warnings:
                    emit("warnings", warnings)
                emit("ingest", ingest(config, db, args.limit))
                emit("parse", parse_stage(config, db, args.limit, args.workers))
                if not args.skip_ocr:
                    emit("ocr", ocr_stage(config, db, None, ocr_workers=args.ocr_workers))
                emit("export-text", export_text_stage(config, db, args.limit))
                emit("extract", extraction_stage(config, db, args.limit, args.workers))
                emit("export", export_stage(config, db))
                emit("status", status(db))
            elif args.command == "process":
                warnings = validate_runtime_config(config)
                if warnings:
                    emit("warnings", warnings)
                emit("ingest", ingest(config, db, args.limit))
                emit("parse", parse_stage(config, db, args.limit, args.workers))
                if not args.skip_ocr:
                    emit("ocr", ocr_stage(config, db, None, ocr_workers=args.ocr_workers))
                emit("export-text", export_text_stage(config, db, args.limit))
                emit("status", status(db))
    except KeyboardInterrupt:
        print("用户中断；已完成的阶段结果已保存在数据库中。", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:  # noqa: BLE001 - CLI error boundary
        print(f"错误: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
