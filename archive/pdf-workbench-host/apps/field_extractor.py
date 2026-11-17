"""独立字段抽取、归一化、校验和人工复核入口。"""

from __future__ import annotations

import argparse

from ._pdf_cli import add_config_argument, add_run_arguments, emit, report_error


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pdf-extract",
        description="PDF字段抽取、标准化、校验、导出和人工复核",
    )
    add_config_argument(parser)
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="自动准备PDF后执行抽取和导出")
    add_run_arguments(run, auto_prepare=True)
    extract = sub.add_parser("extract-only", help="只抽取已解析文档")
    extract.add_argument("--limit", type=int)
    extract.add_argument("--workers", type=int)
    sub.add_parser("export", help="重新导出Excel和CSV")
    sub.add_parser("status", help="查看抽取和复核状态")
    sub.add_parser("validate", help="校验字段、安全正则和运行配置")
    fields = sub.add_parser("fields", help="列出当前字段模板")
    fields.add_argument("--json", action="store_true")
    review = sub.add_parser("apply-review", help="导入人工复核结果")
    review.add_argument("--file", required=True)
    sub.add_parser("reset", help="清除抽取结果并保留解析文本")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        from ..pdfx.config import load_config
        from ..pdfx.database import Database
        from ..pdfx.exporter import export_stage
        from ..pdfx.extraction import extraction_stage
        from ..pdfx.project import validate_project_template
        from ..pdfx.review import apply_review
        from ..pdfx.service import database_status, run_extraction

        if args.command == "validate":
            result = validate_project_template(args.config)
            emit(result)
            return 0 if result["valid"] else 2
        config = load_config(args.config)
        if args.command == "fields":
            values = [
                {
                    "name": item.name,
                    "label": item.label,
                    "type": item.type,
                    "required": item.required,
                    "aliases": item.aliases,
                    "patterns": item.patterns,
                }
                for item in config.fields
            ]
            if args.json:
                emit(values)
            else:
                for item in values:
                    print(
                        f"{item['name']:<28} {item['label']} "
                        f"type={item['type']} required={item['required']}"
                    )
            return 0
        if args.command == "run":
            emit(
                run_extraction(
                    args.config,
                    limit=args.limit,
                    workers=args.workers,
                    auto_prepare=not args.no_auto_prepare,
                    run_ocr=not args.skip_ocr,
                )
            )
            return 0
        with Database(config.database) as database:
            if args.command == "extract-only":
                emit(extraction_stage(config, database, args.limit, args.workers))
            elif args.command == "export":
                emit(export_stage(config, database))
            elif args.command == "status":
                emit(database_status(database))
            elif args.command == "apply-review":
                emit(apply_review(config, database, args.file))
            elif args.command == "reset":
                database.reset_stage("extract")
                emit({"reset": "extract"})
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        return report_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
