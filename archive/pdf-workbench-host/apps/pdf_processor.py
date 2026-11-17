"""独立 PDF 解析、OCR 分流和通用文本导出入口。"""

from __future__ import annotations

import argparse

from ._pdf_cli import add_config_argument, add_run_arguments, emit, report_error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-process",
        description="来源无关的 PDF 批量解析、OCR 分流和文本导出",
    )
    add_config_argument(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="扫描、解析、按需OCR并导出文本")
    add_run_arguments(run)
    sub.add_parser("status", help="查看处理状态")
    sub.add_parser("export-text", help="重新导出TXT、逐页JSONL和清单")
    sub.add_parser("validate", help="校验项目路径、OCR和字段模板")
    reset = sub.add_parser("reset", help="重置解析或OCR阶段")
    reset.add_argument("stage", choices=["parse", "ocr"])
    init = sub.add_parser("init", help="从字段模板创建PDF项目")
    init.add_argument("--template", required=True)
    init.add_argument("--output-config", required=True)
    init.add_argument("--name", default="PDF处理项目")
    init.add_argument("--input", required=True)
    init.add_argument("--work", required=True)
    init.add_argument("--output", required=True)
    init.add_argument("--ocr", choices=["none", "paddle", "tesseract"], default="none")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from ..pdfx.config import load_config
        from ..pdfx.database import Database
        from ..pdfx.project import create_project_config, validate_project_template
        from ..pdfx.service import database_status, run_processing
        from ..pdfx.text_export import export_text_stage

        if args.command == "init":
            path = create_project_config(
                args.template,
                args.output_config,
                project_name=args.name,
                input_dir=args.input,
                work_dir=args.work,
                output_dir=args.output,
                ocr_backend=args.ocr,
            )
            emit({"created": str(path)})
            return 0
        if args.command == "validate":
            result = validate_project_template(args.config)
            emit(result)
            return 0 if result["valid"] else 2
        if args.command == "run":
            emit(
                run_processing(
                    args.config,
                    limit=args.limit,
                    workers=args.workers,
                    run_ocr=not args.skip_ocr,
                )
            )
            return 0
        config = load_config(args.config)
        with Database(config.database) as database:
            if args.command == "status":
                emit(database_status(database))
            elif args.command == "export-text":
                emit(export_text_stage(config, database))
            elif args.command == "reset":
                database.reset_stage(args.stage)
                emit({"reset": args.stage})
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        return report_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
