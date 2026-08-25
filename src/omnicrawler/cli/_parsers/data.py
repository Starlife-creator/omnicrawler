"""数据交付域：research-package / backup / transform / convert / import-easyspider。"""

from __future__ import annotations

import argparse


def configure(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    package = sub.add_parser("research-package", help="创建脱敏、带校验和的研究复现包")
    package.add_argument("--config", "-c", required=True)
    package.add_argument("--output", "-o", required=True)
    package.add_argument("--include-raw", action="store_true")
    backup = sub.add_parser("backup", help="创建或恢复校验和备份")
    backup_sub = backup.add_subparsers(dest="backup_command", required=True)
    backup_create = backup_sub.add_parser("create")
    backup_create.add_argument("--config", "-c", required=True)
    backup_create.add_argument("--output", "-o", required=True)
    backup_create.add_argument("--include-raw", action="store_true")
    backup_restore = backup_sub.add_parser("restore")
    backup_restore.add_argument("package")
    backup_restore.add_argument("--target", required=True)
    transform_cmd = sub.add_parser("transform", help="值级数据变换：--map 表达式追加解析列（--confirm 才写文件）")
    transform_cmd.add_argument("source", help="源数据文件（CSV/JSONL）")
    transform_cmd.add_argument("target", nargs="?", default=None, help="输出文件（--confirm 时必填）")
    transform_cmd.add_argument("--map", action="append", default=[], help="'列名 = 表达式'，可多次；结果追加到 {列名}_parsed 列")
    transform_cmd.add_argument("--transform-steps", default=None, help="旧步骤列表（JSON 数组或 @file），值级翻译为等价 --map")
    transform_cmd.add_argument("--from", dest="src_format", default=None, help="显式源格式（csv/jsonl），默认按扩展名推断")
    transform_cmd.add_argument("--to", dest="dst_format", default=None, help="显式目标格式（csv/jsonl），默认按扩展名推断")
    transform_cmd.add_argument("--dry-run", action="store_true", help="预览：展示前 N 条新旧列对照，不写文件")
    transform_cmd.add_argument("--confirm", action="store_true", help="确认写入输出文件（默认不写）")
    transform_cmd.add_argument("--batch-size", type=int, default=1000, help="求值分批大小（默认 1000）")
    transform_cmd.add_argument("--max-records", type=int, default=None, help="最多处理记录数（默认全部）")
    transform_cmd.add_argument("--on-error", choices=["skip", "abort"], default="skip", help="单条解析/求值错误策略")
    transform_cmd.add_argument("--preview-limit", type=int, default=5, help="dry-run 预览条数")
    convert = sub.add_parser(
        "convert",
        help="P3-2 任意格式互转：CSV/JSONL/XLSX/Parquet/DuckDB 两两互转（不依赖 pipeline 重跑）",
    )
    convert.add_argument("--from", "-f", dest="src", required=True, help="源文件路径（按后缀或 --src-format 判定格式）")
    convert.add_argument("--to", "-t", dest="dst", required=True, help="目标文件路径")
    convert.add_argument("--src-format", help="显式指定源格式（.jsonl / .csv / .xlsx / .parquet / .duckdb）")
    convert.add_argument("--dst-format", help="显式指定目标格式，同上")
    convert.add_argument("--flat", action="store_true", default=True, help="JSONL Reader 把 .data 嵌套展开为 flat 列（默认开）")
    convert.add_argument("--nested", action="store_true", help="JSONL Writer 按 pipeline 原始 records.jsonl 结构：{record_id, source_url, data:{...}, evidence:{...}}")
    convert.add_argument("--table", default="records", help="DuckDB 读写时使用的表名（默认 records）")
    convert.add_argument("--compression", default="zstd", help="Parquet 压缩（默认 zstd）")
    convert.add_argument("--quiet", action="store_true", help="仅输出结果 JSON，不打印进度提示")
    # EasySpider 导入
    import_es = sub.add_parser("import-easyspider", help="将 EasySpider JSON 任务转换为 OmniCrawler YAML 配置")
    import_es.add_argument("json", help="EasySpider 任务 JSON 文件")
    import_es.add_argument("-o", "--output", help="输出 YAML 路径（默认 stdout）")
    import_es.add_argument("--ir", action="store_true", help="输出 Task IR JSON 而非 YAML")
