"""模板域：templates（含 list/recommend/render/validate/export-pack/import-pack/
inspect/diff/merge 子命令）。"""

from __future__ import annotations

import argparse


def configure(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    templates = sub.add_parser("templates", help="搜索、识别和生成采集模板")
    templates_sub = templates.add_subparsers(dest="templates_command", required=True)
    template_list = templates_sub.add_parser("list", help="列出内置和用户模板")
    template_list.add_argument("--query", "-q", default="")
    template_list.add_argument("--category", default="")
    template_list.add_argument("--tag", action="append", default=[])
    template_list.add_argument("--capability", action="append", default=[])
    template_recommend = templates_sub.add_parser("recommend", help="根据URL和页面证据推荐模板")
    template_recommend.add_argument("url", nargs="?", default="")
    template_recommend.add_argument("--url", dest="url_option", default="")
    template_recommend.add_argument("--header", action="append", default=[], metavar="NAME:VALUE")
    template_recommend.add_argument("--body-file")
    template_recommend.add_argument("--json-file")
    template_recommend.add_argument("--limit", type=int, default=5)
    template_render = templates_sub.add_parser("render", help="填充模板变量并生成可运行配置")
    template_render.add_argument("template_id")
    template_render.add_argument("--set", action="append", default=[], metavar="NAME=VALUE")
    template_render.add_argument("--output", "-o", required=True)
    template_render.add_argument("--force", action="store_true")
    template_validate = templates_sub.add_parser("validate", help="离线检查模板元数据和配置契约")
    template_validate.add_argument("--include-legacy", action="store_true")
    template_export = templates_sub.add_parser("export-pack", help="导出可校验的模板包")
    template_export.add_argument("template_id", nargs="+")
    template_export.add_argument("--output", "-o", required=True)
    template_import = templates_sub.add_parser("import-pack", help="安全导入模板包到用户目录")
    template_import.add_argument("pack")
    template_import.add_argument("--target", required=True)
    template_import.add_argument("--overwrite", action="store_true")
    template_inspect = templates_sub.add_parser("inspect", help="安全探测公开网址并自动推荐模板")
    template_inspect.add_argument("url")
    template_inspect.add_argument("--timeout", type=float, default=20.0)
    template_diff = templates_sub.add_parser("diff", help="对比两个模板版本的字段级变化")
    template_diff.add_argument("before")
    template_diff.add_argument("after")
    template_merge = templates_sub.add_parser("merge", help="三方合并模板升级并保留用户自定义项")
    template_merge.add_argument("base", help="用户最初采用的模板")
    template_merge.add_argument("user", help="当前用户配置")
    template_merge.add_argument("update", help="新版模板")
    template_merge.add_argument("--output", "-o", required=True)
    template_merge.add_argument("--force", action="store_true")
