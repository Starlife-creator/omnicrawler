"""模板域：templates（含 list/recommend/render/validate/export-pack/import-pack/
inspect/diff/merge 子命令）。

选项 help 是 **agent 面的一部分**：`AGENTS.md` 写明「CLI 即 agent 面」，
agent 只能靠 `--help` 认识参数。`tools/gates/check_agent_surface_help.py`
守着这一点（基线只许降低不许上升）。
"""

from __future__ import annotations

import argparse


def configure(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    templates = sub.add_parser("templates", help="搜索、识别和生成采集模板")
    templates_sub = templates.add_subparsers(dest="templates_command", required=True)

    template_list = templates_sub.add_parser("list", help="列出内置和用户模板")
    template_list.add_argument("--query", "-q", default="", help="按名称/描述模糊搜索的关键词")
    template_list.add_argument("--category", default="", help="只列出该分类（如 news / ecommerce）")
    template_list.add_argument(
        "--tag", action="append", default=[], help="按标签过滤；可重复，多次出现为「或」关系"
    )
    template_list.add_argument(
        "--capability",
        action="append",
        default=[],
        help="按所需能力过滤（如 js_render / login）；可重复",
    )

    template_recommend = templates_sub.add_parser("recommend", help="根据URL和页面证据推荐模板")
    template_recommend.add_argument("url", nargs="?", default="", help="目标网址（位置参数）；也可用 --url")
    template_recommend.add_argument(
        "--url", dest="url_option", default="", help="目标网址（选项形式，与位置参数二选一）"
    )
    template_recommend.add_argument(
        "--header",
        action="append",
        default=[],
        metavar="NAME:VALUE",
        help="附加请求头，形如 Cookie:abc；可重复",
    )
    template_recommend.add_argument("--body-file", help="页面 HTML 样本文件路径（离线推荐用）")
    template_recommend.add_argument("--json-file", help="页面 JSON 响应样本文件路径")
    template_recommend.add_argument("--limit", type=int, default=5, help="最多返回几条推荐（默认 5）")

    template_render = templates_sub.add_parser("render", help="填充模板变量并生成可运行配置")
    template_render.add_argument("template_id", help="模板 id（见 templates list）")
    template_render.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="设置一个模板变量，可重复；缺的变量按模板默认值填充",
    )
    template_render.add_argument("--output", "-o", required=True, help="生成的配置文件输出路径")
    template_render.add_argument("--force", action="store_true", help="输出文件已存在时覆盖")

    capture = templates_sub.add_parser(
        "capture", help="从已试跑简单公开 HTTP 任务保存脱敏模板，摘要仅作历史参考"
    )
    capture.add_argument("template_id", help="新模板的 id（会在用户模板目录创建）")
    capture.add_argument("--config", "-c", required=True, help="已成功试跑过的任务配置路径")
    capture.add_argument(
        "--acceptance", required=True, help="sample 产生的 preflight_acceptance.json"
    )
    capture.add_argument("--parameters", help="参数名到 path/type/required/范围的 JSON 声明")
    capture.add_argument("--output", "-o", required=True, help="模板包输出路径")
    capture.add_argument("--force", action="store_true", help="输出已存在时覆盖")

    template_validate = templates_sub.add_parser("validate", help="离线检查模板元数据和配置契约")
    template_validate.add_argument(
        "--include-legacy", action="store_true", help="同时校验旧版（legacy）模板"
    )

    fixture_check = templates_sub.add_parser(
        "verify-fixture", help="离线复验固定HTML/JSON样本；失败暂离默认推荐"
    )
    fixture_check.add_argument("template_id", help="要复验的模板 id")
    fixture_check.add_argument("--url", required=True, help="样本对应的请求网址")
    fixture_check.add_argument("--body-file", required=True, help="保存的 HTML/JSON 响应样本文件")
    fixture_check.add_argument(
        "--expected", required=True, help="非空原始提取记录JSON列表"
    )
    fixture_check.add_argument(
        "--set", action="append", default=[], metavar="NAME=VALUE",
        help="复验时覆盖的模板变量，可重复",
    )

    fixture_history = templates_sub.add_parser(
        "verification-history", help="查看模板复验成功、失败及历史版本"
    )
    fixture_history.add_argument("template_id", help="要查看历史记录的模板 id")

    template_export = templates_sub.add_parser("export-pack", help="导出可校验的模板包")
    template_export.add_argument("template_id", nargs="+", help="要导出的模板 id；可一次给多个")
    template_export.add_argument("--output", "-o", required=True, help="模板包输出路径")

    template_import = templates_sub.add_parser("import-pack", help="安全导入模板包到用户目录")
    template_import.add_argument("pack", help="要导入的模板包文件路径")
    template_import.add_argument("--target", required=True, help="导入到的目标目录")
    template_import.add_argument(
        "--overwrite", action="store_true", help="目标已存在同名模板时覆盖"
    )

    template_inspect = templates_sub.add_parser("inspect", help="安全探测公开网址并自动推荐模板")
    template_inspect.add_argument("url", help="要探测的公开网址")
    template_inspect.add_argument(
        "--timeout", type=float, default=20.0, help="单次请求超时秒数（默认 20）"
    )

    template_diff = templates_sub.add_parser("diff", help="对比两个模板版本的字段级变化")
    template_diff.add_argument("before", help="旧版模板 id 或路径")
    template_diff.add_argument("after", help="新版模板 id 或路径")

    template_merge = templates_sub.add_parser("merge", help="三方合并模板升级并保留用户自定义项")
    template_merge.add_argument("base", help="用户最初采用的模板")
    template_merge.add_argument("user", help="当前用户配置")
    template_merge.add_argument("update", help="新版模板")
    template_merge.add_argument("--output", "-o", required=True, help="合并结果输出路径")
    template_merge.add_argument("--force", action="store_true", help="输出已存在时覆盖")
