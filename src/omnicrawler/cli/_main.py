from __future__ import annotations

import argparse
import io
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

from .. import __version__
from ..core.logging_utils import configure_logging
from ..core.runtime_paths import configure_runtime_environment
from ..core.utils import user_agent
from ..services.benchmarking import BenchmarkHistory, BenchmarkRunner

# Source installs discover project-local .runtime assets, while frozen builds
# discover the browser/OCR runtime beside the companion executable.
configure_runtime_environment()


def _json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def _complete_seed_scheme(seed: str) -> str | None:
    """为入口网址补全 http(s) scheme。

    对 Windows 盘符路径 / 反斜杠路径 / 相对路径返回 None，避免把
    ``C:\\data\\page.html`` 误补全成 ``https://C:\\data\\page.html``（F688）。
    """
    if not seed or "\\" in seed or re.match(r"^[A-Za-z]:[\\/]", seed) or seed.startswith(("/", ".", "\\")):
        return None
    has_scheme = re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*://", seed) is not None
    return seed if has_scheme else f"https://{seed}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="omnicrawler", description="模块化网站采集、附件下载与PDF字段抽取平台")
    parser.add_argument("--version", action="version", version=f"omnicrawler {__version__}")
    parser.add_argument("--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-format", default="text", choices=["text", "json"])
    sub = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (("run", "启动或重新运行任务"), ("resume", "从中断队列继续")):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("--config", "-c", required=True)
        item.add_argument("--max-pages", type=int)
        item.add_argument("--progress", action="store_true", help="显示实时采集进度条")
        item.add_argument(
            "--strict", action="store_true",
            help="严格模式: 0 条有效记录时退出码为 1(默认向前兼容)",
        )
        if name == "resume":
            item.add_argument("--retry-failed", action="store_true", help="重试死信队列中的失败请求")
    validate = sub.add_parser("validate", help="校验配置")
    validate.add_argument("--config", "-c", required=True)
    doctor = sub.add_parser("doctor", help="检查配置、依赖和磁盘")
    doctor.add_argument("--config", "-c", required=True)
    status = sub.add_parser("status", help="查看断点库状态")
    status.add_argument("--config", "-c", required=True)
    status.add_argument("--format", default="json", choices=["json", "text"], help="输出格式 (json 用于脚本, text 人类可读)")
    export = sub.add_parser("export", help="重新导出数据库结果")
    export.add_argument("--config", "-c", required=True)
    export.add_argument("--run-id")
    reprocess = sub.add_parser(
        "reprocess",
        help="Re-run extraction, quality checks and export from raw archives without downloading again",
    )
    reprocess.add_argument("--config", "-c", required=True)
    reprocess.add_argument("--run-id")
    plugins = sub.add_parser("plugins", help="列出已注册插件 / 本地插件自检")
    plugins.add_argument("--config", "-c")
    # Phase 1（B5）：plugins audit --local <dir> 本地自检（许可+凭据，与 CI 门 2 同逻辑）
    plugins.add_argument("plugins_command", nargs="?", default=None, help="子命令：audit（可选）")
    plugins.add_argument("--local", default=None, help="audit 子命令：审计的本地插件目录")
    # Phase 2a（B5/H4）：plugins audit --report 生成脱敏环境诊断报告
    plugins.add_argument("--report", action="store_true", help="audit 子命令：生成脱敏环境诊断报告")
    # Phase 2b（H4 第 66 轮④）：plugins audit --export-egress <file> SIEM 共现导出
    plugins.add_argument(
        "--export-egress", default=None, metavar="FILE",
        help="audit 子命令：导出共现事件 JSONL（SIEM 关联分析，固定字段清单）",
    )
    # Phase 3（P1 第 67 轮）：plugins scaffold-contract2 —— 新建契约 2 工程骨架
    plugins.add_argument(
        "--plugin-id", default=None, help="scaffold-contract2 子命令：新插件 ID（小写字母开头）"
    )
    plugins.add_argument(
        "--display-name", default=None, help="scaffold-contract2 子命令：插件显示名"
    )
    plugins.add_argument(
        "--output-dir", default=".", help="scaffold-contract2 子命令：输出根目录（默认当前目录）"
    )
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
    init = sub.add_parser("init", help="复制一个可编辑的项目配置")
    init.add_argument("name")
    init.add_argument("--template", default="static_html")
    init.add_argument("--output", default="configs")
    wizard = sub.add_parser("wizard", help="交互生成基础配置")
    wizard.add_argument("--output", default="configs/new_project.yaml")
    server = sub.add_parser("serve", help="启动只读监控面板")
    server.add_argument("--config", "-c", required=True)
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8765)
    sub.add_parser("workbench", help="启动从采集到PDF结果的统一桌面工作台")
    schedule = sub.add_parser("schedule", help="管理可恢复的本地定时任务")
    schedule.add_argument("--database", default="work/schedules.sqlite3")
    schedule_sub = schedule.add_subparsers(dest="schedule_command", required=True)
    schedule_add = schedule_sub.add_parser("add", help="添加按固定间隔运行的任务")
    schedule_add.add_argument("--config", "-c", required=True)
    schedule_add.add_argument("--name", default="")
    schedule_add.add_argument("--every-seconds", type=int, required=True)
    schedule_add.add_argument("--require-ac", action="store_true")
    schedule_add.add_argument("--require-network", action="store_true")
    schedule_add.add_argument("--minimum-battery", type=float, default=0)
    schedule_sub.add_parser("list", help="列出定时任务")
    schedule_run = schedule_sub.add_parser("run-due", help="领取并运行当前到期任务")
    schedule_run.add_argument("--limit", type=int, default=10)
    migrate = sub.add_parser("migrate", help="把旧配置安全迁移为当前版本")
    migrate.add_argument("--config", "-c", required=True)
    migrate.add_argument("--output", "-o", required=True)
    migrate.add_argument("--force", action="store_true")
    cleanup = sub.add_parser("cleanup", help="预览或执行配置的数据保留策略")
    cleanup.add_argument("--config", "-c", required=True)
    cleanup.add_argument("--apply", action="store_true", help="实际删除；省略时只输出计划")
    field_suggest = sub.add_parser("field-suggest", help="从保存的 HTML 自动推荐稳定字段选择器")
    field_suggest.add_argument("html")
    field_suggest.add_argument("--limit", type=int, default=100)
    field_suggest.add_argument("--output", "-o")
    recorder = sub.add_parser("record-actions", help="打开浏览器并录制点击、输入与滚动操作")
    recorder.add_argument("url")
    recorder.add_argument("--output", "-o", required=True)
    recorder.add_argument("--timeout", type=int, default=300)
    api = sub.add_parser("api-discover", help="从浏览器 API 捕获 JSON 生成 REST 模板")
    api.add_argument("input")
    api.add_argument("--output", "-o", required=True)
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
    preflight = sub.add_parser("preflight", help="运行前检查依赖、磁盘、配置和资源估算")
    preflight.add_argument("--config", "-c", required=True)
    sample = sub.add_parser("sample", help="在独立工作区执行 1-10 页小样本试跑")
    sample.add_argument("--config", "-c", required=True)
    sample.add_argument("--pages", type=int, default=3)
    control = sub.add_parser("control", help="暂停、继续或请求安全停止正在运行的任务")
    control.add_argument("--config", "-c", required=True)
    control.add_argument("action", choices=["status", "pause", "resume", "stop"])
    compare = sub.add_parser("compare-runs", help="对比两次运行的新增、删除和字段变化")
    compare.add_argument("--config", "-c", required=True)
    compare.add_argument("before_run")
    compare.add_argument("after_run")
    compare.add_argument("--output", "-o")
    regression = sub.add_parser("regression", help="离线验证已保存的网页/API 回归样本")
    regression.add_argument("--config", "-c", required=True)
    capabilities = sub.add_parser("capabilities", help="分层检查当前任务所需的 Python、浏览器、OCR 和存储能力")
    capabilities.add_argument("--mode", choices=["quick", "task", "deep"], default="quick")
    capabilities.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="FEATURE",
        help="task 模式需要的能力，可重复：web/pdf/browser/ocr-tesseract/ocr-paddle/gui/streams/storage-*",
    )
    capabilities.add_argument("--verify-imports", action="store_true", help="兼容选项：实际导入全部已安装模块（较慢）")
    capabilities.add_argument("--self-test", action="store_true", help="用生成样本离线运行 Tesseract 与 PaddleOCR（分钟级）")
    capabilities.add_argument("--portable-paths", action="store_true", help="把运行路径写成可移植占位符")
    security_report = sub.add_parser("security-report", help="汇总任务实际网络访问边界和安全例外")
    security_report.add_argument("--config", "-c", required=True)
    recovery = sub.add_parser("recovery", help="查看并执行任务恢复中心操作")
    recovery.add_argument("--config", "-c", required=True)
    recovery.add_argument(
        "action", choices=["overview", "continue", "retry-failed", "relogin", "reprocess", "rollback-config"]
    )
    recovery.add_argument("--limit", type=int)
    recovery.add_argument("--backup", help="rollback-config使用的已验证配置备份")
    recovery.add_argument("--apply", "--yes", action="store_true", help="执行破坏性操作（rollback-config 需要）")
    plan = sub.add_parser("plan", help="把任务编译为可解释、可校验哈希的执行计划")
    plan.add_argument("--config", "-c", required=True)
    plan.add_argument("--compare", help="与另一配置的计划进行字段级差异比较")
    plan.add_argument("--output", "-o")
    worker = sub.add_parser("worker", help="启动或重新连接认证的独立本地Worker")
    worker.add_argument("--config", "-c", required=True)
    worker.add_argument("action", choices=["start", "status", "pause", "resume", "stop", "shutdown"])
    worker.add_argument("--session")
    queue_cmd = sub.add_parser("queue", help="远程任务调度队列（Redis 可用时共享，否则本地降级）")
    queue_sub = queue_cmd.add_subparsers(dest="action", required=True)
    q_submit = queue_sub.add_parser("submit", help="提交配置任务到队列")
    q_submit.add_argument("--config", "-c", required=True, help="任务配置文件")
    q_submit.add_argument("--redis", dest="redis_url", default=None, help="Redis URL，如 redis://localhost:6379/0")
    q_submit.add_argument("--local-path", default=None, help="本地降级队列的 SQLite 文件路径")
    q_status = queue_sub.add_parser("status", help="查看后端类型、队列深度与 worker 心跳")
    q_status.add_argument("--redis", dest="redis_url", default=None, help="Redis URL")
    q_status.add_argument("--local-path", default=None, help="本地降级队列的 SQLite 文件路径")
    q_consume = queue_sub.add_parser("consume", help="以 worker 身份持续消费并执行任务")
    q_consume.add_argument("--redis", dest="redis_url", default=None, help="Redis URL")
    q_consume.add_argument("--local-path", default=None, help="本地降级队列的 SQLite 文件路径")
    q_consume.add_argument("--worker-id", default="", help="worker 标识（默认 hostname-pid）")
    q_consume.add_argument("--interval", type=float, default=1.0, help="空队列轮询间隔（秒）")
    q_consume.add_argument("--max-tasks", type=int, default=None, help="最多执行任务数（默认无限）")
    q_consume.add_argument("--executor", choices=["backend", "pipeline"], default="backend", help="任务执行方式")
    scene_cmd = sub.add_parser("scene", help="场景/槽位/基因管理（DB 单一真源，批 C）")
    scene_sub = scene_cmd.add_subparsers(dest="scene_command", required=True)
    scene_import = scene_sub.add_parser("import", help="导入场景定义（缺省 bundled 出厂默认）")
    scene_import.add_argument("--config", "-c", required=True)
    scene_import.add_argument("--path", default="", help="用户场景 YAML 路径；缺省导入包内 scenes/*.yaml")
    scene_list = scene_sub.add_parser("list", help="列出全部场景（槽位数 / 基因数）")
    scene_list.add_argument("--config", "-c", required=True)
    scene_show = scene_sub.add_parser("show", help="单场景体检报告")
    scene_show.add_argument("scene")
    scene_show.add_argument("--config", "-c", required=True)
    scene_candidates = scene_sub.add_parser("candidates", help="列出抽取候选")
    scene_candidates.add_argument("--config", "-c", required=True)
    scene_candidates.add_argument("--scene", default="", help="按场景过滤")
    scene_candidates.add_argument("--pending", action="store_true", help="只看未验收候选")
    scene_candidates.add_argument("--accepted", action="store_true", help="只看已验收候选")
    scene_candidates.add_argument("--limit", type=int, default=100)
    scene_accept = scene_sub.add_parser("accept", help="验收抽取候选")
    scene_accept.add_argument("candidate_id", type=int)
    scene_accept.add_argument("--config", "-c", required=True)
    scene_maintenance = scene_sub.add_parser("maintenance", help="淘汰低适应度基因（删除操作，需 --apply）")
    scene_maintenance.add_argument("--config", "-c", required=True)
    scene_maintenance.add_argument("--scene", default="", help="按场景过滤")
    scene_maintenance.add_argument("--min-fitness", type=float, default=0.2)
    scene_maintenance.add_argument("--min-trials", type=int, default=3)
    scene_maintenance.add_argument("--apply", "--yes", action="store_true", help="执行淘汰；省略时只预览")
    timeline = sub.add_parser("timeline", help="查看证据胶囊时间线（run 内提取动作序列）")
    timeline.add_argument("--config", "-c", required=True)
    timeline.add_argument("--run", default="", help="run_id；省略时列出全部 run 的胶囊统计")
    timeline.add_argument("--capsule-dir", default=None, help="胶囊日志目录（默认 <workspace>/capsules）")
    timeline.add_argument("--limit", type=int, default=50, help="时间线条目上限")
    replay_cmd = sub.add_parser("replay", help="基于胶囊 + 归档 raw 限定重放字段提取")
    replay_cmd.add_argument("--config", "-c", required=True)
    replay_cmd.add_argument("--run", required=True, help="run_id")
    replay_cmd.add_argument("--field", required=True, help="要重放的字段名")
    replay_cmd.add_argument("--stage", default="extract", help="胶囊阶段（默认 extract）")
    replay_cmd.add_argument("--capsule-dir", default=None, help="胶囊日志目录（默认 <workspace>/capsules）")
    replay_cmd.add_argument("--timeout", type=float, default=10.0, help="重放子进程超时秒数")
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
    workspace = sub.add_parser("workspace", help="管理项目工作区、体检、打包、快照和回滚")
    workspace.add_argument("--config", "-c", required=True)
    workspace.add_argument("action", choices=["init", "health", "package", "snapshot", "rollback"])
    workspace.add_argument("--target")
    workspace.add_argument("--kind", choices=["full", "config", "support"], default="full")
    workspace.add_argument("--apply", "--yes", action="store_true", help="执行破坏性操作（rollback 需要）")
    components = sub.add_parser("components", help="查看、验证、离线导入或卸载可选组件")
    components.add_argument("action", choices=["list", "inspect", "stage", "import", "uninstall", "rollback"])
    components.add_argument("--package")
    components.add_argument("--name")
    components.add_argument("--sha256")
    components.add_argument("--allow-unsigned", action="store_true", help="仅用于本地开发包")
    components.add_argument("--apply", "--yes", action="store_true", help="执行破坏性操作（uninstall/rollback 需要）")
    runtime_verify = sub.add_parser("runtime-verify", help="验证便携运行时清单是否缺失或被篡改")
    runtime_verify.add_argument("--root", default=".")
    # EasySpider 导入
    import_es = sub.add_parser("import-easyspider", help="将 EasySpider JSON 任务转换为 OmniCrawler YAML 配置")
    import_es.add_argument("json", help="EasySpider 任务 JSON 文件")
    import_es.add_argument("-o", "--output", help="输出 YAML 路径（默认 stdout）")
    import_es.add_argument("--ir", action="store_true", help="输出 Task IR JSON 而非 YAML")
    # 可视化选择器
    visual_sel = sub.add_parser("visual-select", help="启动浏览器可视化元素选择器 WebSocket 服务")
    visual_sel.add_argument("--port", type=int, default=8084, help="WebSocket 端口（默认 8084）")
    visual_sel.add_argument("--output", "-o", help="自动写入的 YAML 配置路径")
    # 智能爬虫
    auto_crawl = sub.add_parser("auto-analyze", help="智能分析页面结构，自动推断字段和分页")
    auto_crawl.add_argument("input", help="HTML 文件路径 或 URL")
    auto_crawl.add_argument("-o", "--output", help="输出 YAML 配置路径")
    auto_crawl.add_argument("--url", help="页面原始 URL")
    c4a = sub.add_parser("c4a-fetch", help="使用 Crawl4AI 进行轻量 JS 渲染抓取")
    c4a.add_argument("url", help="目标 URL")
    c4a.add_argument("--stealth", action="store_true", help="使用 undetected 浏览器模式")
    c4a.add_argument("--extract", help="CSS 提取 schema JSON 文件")
    c4a.add_argument("-o", "--output", help="输出 JSON 文件路径")
    # 反检测增强
    stealth_cmd = sub.add_parser("stealth-fingerprint", help="生成随机浏览器指纹（反检测）")
    stealth_cmd.add_argument("--count", type=int, default=1, help="生成数量")
    stealth_cmd.add_argument("--json", action="store_true", help="使用 JSON 输出")
    # Apify 模板生成
    tmpl_gen = sub.add_parser("gen-templates", help="根据 Apify 130+ 平台知识生成站点模板")
    tmpl_gen.add_argument("--list", action="store_true", help="列出所有已知平台")
    tmpl_gen.add_argument("--generate", metavar="PLATFORM", help="生成指定平台模板")
    tmpl_gen.add_argument("--all", metavar="DIR", help="生成所有平台模板到目录")
    # 性能基准测试
    benchmark = sub.add_parser("benchmark", help="运行性能基准测试并对比历史基线")
    benchmark.add_argument("--config", "-c", required=True, help="跑分所用的 YAML 配置")
    benchmark.add_argument(
        "--profile", choices=["low", "standard", "high", "all"],
        default="all", help="测试用例 (默认: all)"
    )
    benchmark.add_argument("--output", "-o", default="bench_history.json",
                           help="基准历史 JSON 输出路径")
    benchmark.add_argument("--history", help="从已有历史 JSON 读取基线（默认使用 --output）")
    benchmark.add_argument("--regression-threshold", type=float, default=0.1,
                           help="吞吐量退化告警阈值（默认 0.1 = 10%%）")
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
    return parser


def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    # 统一 UTF-8 输出：Windows 管道/重定向下 Python 默认按 locale（cp1252/GBK）
    # 编码 stdout，打印含中文的 JSON（_json ensure_ascii=False）会抛
    # UnicodeEncodeError——CI（capabilities --verify-imports）与任何重定向
    # 场景都受影响。交互控制台（PEP 528）本就是 UTF-8，此处只修正管道形态。
    stdout = sys.stdout
    stderr = sys.stderr
    if isinstance(stdout, io.TextIOWrapper) and isinstance(stderr, io.TextIOWrapper):
        stdout.reconfigure(encoding="utf-8", errors="replace")
        stderr.reconfigure(encoding="utf-8", errors="replace")
    # PDF sub-commands use their own entry points
    if argv and argv[0] in {"pdf", "pdf-process", "pdf-extract"}:
        _dispatch_pdf(argv)
        return
    if not argv:
        _print_welcome()
        return
    try:
        args = build_parser().parse_args(argv)
    except SystemExit:
        # argparse emits its own usage on --help / invalid args; add a friendly nudge
        if not any(a in argv for a in ("-h", "--help")):
            print("\n💡 提示: 运行 omnicrawler wizard 开始交互创建配置，或 omnicrawler --help 查看全部命令", file=sys.stderr)
        raise
    configure_logging(args.log_level, args.log_format)
    # S4.2 ③：启动第一行日志打印关键路径（data_dir/config_path），排障不迷路
    import logging as _logging

    _root_logger = _logging.getLogger("omnicrawler")
    _root_logger.info("omnicrawler %s 启动; data_dir=%s", __version__, _data_dir_hint())
    if getattr(args, "config", None):
        _root_logger.info("config_path=%s", args.config)
    try:
        _dispatch(args)
    except KeyboardInterrupt:
        print("已中断；已完成状态保存在SQLite中，可用resume继续。", file=sys.stderr)
        raise SystemExit(130)
    except Exception as exc:  # CLI error boundary.
        print(f"错误: {type(exc).__name__}: {exc}", file=sys.stderr)
        _print_error_hint(exc)
        raise SystemExit(1)


def _data_dir_hint() -> str:
    """S4.2 ③：数据目录提示——便携数据根/本地数据根，不指向安装目录。"""
    from ..core.runtime_paths import portable_data_root

    try:
        return str(portable_data_root())
    except Exception:  # noqa: BLE001 - 兜底不阻断启动
        return "（数据目录解析失败）"


def _print_welcome() -> None:
    print(f"""
╔══════════════════════════════════════════════════╗
║       OmniCrawler {__version__} — 模块化网站采集平台      ║
╚══════════════════════════════════════════════════╝

  快速开始:
    omnicrawler wizard              交互创建配置文件
    omnicrawler workbench           启动图形工作台
    omnicrawler init <名称>         从模板创建项目
      --template static_html

  常用命令:
    omnicrawler run    -c <配置>    运行采集任务
    omnicrawler status -c <配置>    查看任务状态
    omnicrawler doctor -c <配置>    诊断配置和环境
    omnicrawler templates list      浏览内置模板库

  详细帮助: omnicrawler -h
  文档: OmniCrawler-用户指南.md
""")


def _print_error_hint(exc: Exception) -> None:
    """追加针对性修复建议到 stderr。"""
    msg = str(exc).lower()
    hints: list[str] = []
    if isinstance(exc, FileNotFoundError):
        hints.append("请确认文件路径正确，或运行 omnicrawler init 创建新配置")
    elif isinstance(exc, (ValueError, KeyError)):
        if "seeds" in msg or "入口" in str(exc):
            hints.append("配置中缺少入口 URL；请在 source.seeds 中至少填写一个网址")
        elif "template" in msg or "模板" in str(exc):
            hints.append("模板不存在；运行 omnicrawler templates list 查看可用模板")
        else:
            hints.append("请运行 omnicrawler doctor -c <配置> 检查配置有效性")
    elif isinstance(exc, PermissionError):
        hints.append("请确认目标网站允许自动访问，或检查网络代理设置")
    elif isinstance(exc, (ConnectionError, TimeoutError)):
        hints.append("网络连接失败；请检查目标网址是否可访问，或尝试降低并发数")
    else:
        hints.append("请运行 omnicrawler doctor 检查环境，或查看 OmniCrawler-用户指南.md")
    if hints:
        print(f"  💡 修复建议: {hints[0]}", file=sys.stderr)


def _dispatch_pdf(argv: list[str]) -> None:
    cmd, rest = argv[0], argv[1:]
    if cmd == "pdf":
        from ..pdfx.cli import main as pdf_main

        old = sys.argv
        try:
            sys.argv = ["omnicrawler pdf", *rest]
            pdf_main()
        finally:
            sys.argv = old
    elif cmd == "pdf-process":
        from ..apps.pdf_processor import main as processor_main

        raise SystemExit(processor_main(rest))
    elif cmd == "pdf-extract":
        from ..apps.field_extractor import main as extractor_main

        raise SystemExit(extractor_main(rest))


def _dispatch(args: argparse.Namespace) -> None:
    """Route via ``_handlers.lookup()`` registry.

    All commands are dispatched through the typed registry in
    ``_handlers.py``. Unknown commands raise an error (should not happen
    since argparse validates command names before dispatch).
    """
    from ._handlers import lookup

    handler = lookup(args.command)
    if handler is None:
        raise ValueError(f"未注册的命令: {args.command}")
    handler(args)


def _print_plan_summary(name: str, kind: str, seed: str, max_pages: int, formats: list[str], email: str) -> None:
    """输出自然语言可读的任务计划摘要。"""
    kind_desc = {
        "static_html": "普通静态网页", "crawl": "站内遍历爬取", "focused": "关键词聚焦采集",
        "rest": "REST API 接口", "browser": "浏览器动态渲染", "feed": "RSS/Atom 订阅",
        "sitemap": "Sitemap 地图",
    }
    plan_lines = [
        f"📋 任务计划: {name}",
        f"   目标: 从 {seed} 采集 {'最多' if max_pages < 1000 else '约'} {max_pages} 页",
        f"   方式: {kind_desc.get(kind, kind)}",
        f"   输出: {', '.join(formats).upper()}",
        f"   联系: {email} (写入 User-Agent)",
    ]
    if kind in ("crawl", "focused"):
        plan_lines.append("   ⚠ 将会跟随站内链接，请确保遵守 robots.txt")
    if kind == "browser":
        plan_lines.append("   ⚠ 需要 Playwright 浏览器（首次使用需 playwright install chromium）")
    print("\n".join(plan_lines))


def _wizard(output: Path) -> None:
    """交互式配置向导：通过多步骤问答创建采集配置文件。"""
    import urllib.parse

    SOURCE_KINDS: dict[str, str] = {
        "static_html": "普通静态网页，无需浏览器渲染",
        "crawl": "从入口开始逐页遍历同站链接（广度优先）",
        "focused": "只采集与关键词相关的页面",
        "rest": "REST API 接口（JSON 数据）",
        "browser": "需要 Playwright 浏览器渲染的动态页面",
        "feed": "RSS/Atom 订阅源",
        "sitemap": "网站的 sitemap.xml 地图",
    }

    print("╔══════════════════════════════════════════════╗")
    print("║     OmniCrawler 交互式配置向导               ║")
    print("╚══════════════════════════════════════════════╝")
    print()
    print("本向导将通过几个简单问题帮你生成采集配置。")
    print("所有问题都可以直接回车使用默认值。按 Ctrl+C 随时退出。")
    print()

    # Step 1: Project name（拒绝路径穿越字符并净化输入）
    _NAME_RE = re.compile(r"[^a-zA-Z0-9_\-\u4e00-\u9fff]")
    while True:
        raw_name = input("① 项目名称 [my_project]: ").strip() or "my_project"
        if ".." in raw_name or "/" in raw_name or "\\" in raw_name:
            print("   ⚠ 项目名称不能包含 '..' '/' '\\' 等路径字符，请重新输入")
            continue
        name = _NAME_RE.sub("_", raw_name).strip("_") or "my_project"
        if name != raw_name:
            print(f"   → 名称已净化为: {name}")
        break
    print(f"   → 工作目录将创建在 work/{name}/\n")

    # Step 2: Source type
    print("② 选择来源类型（输入编号或名称）:")
    kinds_list = list(SOURCE_KINDS.items())
    for i, (key, desc) in enumerate(kinds_list, 1):
        print(f"   {i}. {key:14s} — {desc}")
    while True:
        choice = input(f"   请选择 [1-{len(kinds_list)}, 默认 crawl]: ").strip()
        if not choice:
            kind = "crawl"
            break
        if choice.isdigit() and 1 <= int(choice) <= len(kinds_list):
            kind = kinds_list[int(choice) - 1][0]
            break
        if choice in SOURCE_KINDS:
            kind = choice
            break
        print(f"   ⚠ 请输入 1-{len(kinds_list)} 之间的数字，或直接输入名称")
    print(f"   → 已选择: {kind} ({SOURCE_KINDS[kind]})\n")

    # Step 3: Seed URL(s)
    print("③ 入口网址（要采集的第一个页面）")
    while True:
        try:
            seed = input("   网址: ").strip()
        except (EOFError, StopIteration) as exc:
            raise ValueError("入口网址不能为空") from exc
        if not seed:
            print("   ⚠ 入口网址不能为空")
            continue
        # F688：不要把 Windows 盘符路径/相对路径误补成 https://C:\data\page.html
        candidate = _complete_seed_scheme(seed)
        if candidate is None:
            print("   ⚠ 网址格式无效，请以 https:// 开头")
            continue
        parsed = urllib.parse.urlparse(candidate)
        if parsed.netloc:
            print(f"   → 已识别域名: {parsed.netloc}")
            break
        print("   ⚠ 网址格式无效，请以 https:// 开头")
    print()

    # Step 4: Max pages
    print("④ 采集上限（防止失控）")
    maximum = input("   最大页面数 [100]: ").strip() or "100"
    try:
        max_pages = int(maximum)
        if max_pages < 1:
            max_pages = 100
    except ValueError:
        max_pages = 100
    print(f"   → 最多采集 {max_pages} 个页面\n")

    # Step 5: Contact email
    print("⑤ 维护者联系方式（填入 User-Agent，方便网站管理员联系你）")
    email = input("   邮箱 [crawler@example.com]: ").strip() or "crawler@example.com"
    print(f"   → User-Agent 将包含: {email}\n")

    # Step 6: Output format
    print("⑥ 输出格式（可多选，逗号分隔）")
    try:
        formats_raw = input("   jsonl / csv / xlsx [默认 jsonl,csv]: ").strip().lower()
    except (EOFError, StopIteration):
        formats_raw = ""
    formats = [f.strip() for f in (formats_raw.split(",") if formats_raw else ["jsonl", "csv"]) if f.strip()]

    # Build config
    data: dict[str, Any] = {
        "project": {"name": name, "workspace": f"work/{name}"},
        "source": {"kind": kind, "seeds": [seed]},
        "crawl": {"max_pages": max_pages, "max_depth": 3, "same_host": True, "concurrency": 4},
        "http": {
            "user_agent": user_agent(f"+contact: {email}"),
            "respect_robots": True,
            "delay_seconds": 1.0,
        },
        "extract": {"mode": "auto", "fields": {"title": {"selector": "title"}, "heading": {"selector": "h1"}}},
        "outputs": {fmt: True for fmt in formats},
    }

    output_path = output.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        raise FileExistsError(f"目标已存在，不会覆盖: {output_path}")
    output_path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")

    print(f"\n✅ 配置已生成: {output_path}")
    print()
    # -- 生成自然语言计划摘要 --
    _print_plan_summary(name, kind, seed, max_pages, formats, email)
    print()
    print("下一步建议操作:")
    print(f"  1. omnicrawler doctor  -c {output_path}     # 检查环境和配置")
    print(f"  2. omnicrawler sample  -c {output_path}     # 试跑 3 页验证")
    print(f"  3. omnicrawler run     -c {output_path}     # 正式采集")
    print()
    print("💡 提示: 如需更精准的字段配置，可以:")
    print(f"   omnicrawler auto-analyze {seed} -o {output_path}")
    print("   omnicrawler visual-select        # 在浏览器中可视化点选元素")
    print()
    _json({"created": str(output_path), "next": f"omnicrawler doctor -c {output_path}"})


def _key_values(items: list[str], separator: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in items:
        if separator not in item:
            raise ValueError(f"参数必须使用 NAME{separator}VALUE 格式: {item}")
        key, value = item.split(separator, 1)
        key = key.strip()
        if not key:
            raise ValueError(f"参数名不能为空: {item}")
        result[key] = value.strip()
    return result


def _run_benchmark(args: argparse.Namespace) -> None:
    """Run performance benchmarks and compare against historical baselines.

    Supports --profile low/standard/high/all and outputs JSON history.
    """
    from pathlib import Path

    profiles_to_run: list[str]
    if args.profile == "all":
        profiles_to_run = ["low", "standard", "high"]
    else:
        profiles_to_run = [args.profile]

    history_path = Path(args.history or args.output)
    history = BenchmarkHistory(history_path)
    runner = BenchmarkRunner()

    print("OmniCrawler 性能基准测试")
    print(f"配置: {args.config}")
    print(f"用例: {', '.join(profiles_to_run)}")
    print(f"历史: {history_path}")
    print()

    failed: list[str] = []
    for profile in profiles_to_run:
        print(f"[{profile}] 运行中...", end=" ", flush=True)
        try:
            result = runner.run(profile, config_path=args.config)
            history.add(result)
            check = history.check_regression(
                result, threshold=float(args.regression_threshold)
            )
            print(f"完成 — {result.pages} 页, {result.pages_per_second:.1f} 页/秒")
            if check.get("regression"):
                change = float(str(check.get("throughput_change", 0))) * 100
                print(f"  ⚠ 性能退化: 吞吐量下降 {abs(change):.1f}% (阈值 {args.regression_threshold * 100:.0f}%)")
            elif check.get("reason") == "no_baseline":
                print("  📊 首次基准记录（无历史基线）")
        except Exception as exc:  # noqa: BLE001
            print(f"失败: {exc}")
            failed.append(profile)

    summary = history.all_results()
    print(f"\n共 {len(summary)} 条基准记录保存至 {history_path}")
    if failed:
        # E10：失败不再静默吞掉——全部 profile 失败时以非零退出码结束
        raise SystemExit(f"基准运行失败: {', '.join(failed)}")


if __name__ == "__main__":
    main()
