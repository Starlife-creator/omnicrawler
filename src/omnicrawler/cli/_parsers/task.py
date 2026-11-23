"""任务执行域：run/resume/validate/doctor/status/export/reprocess/sample/
control/preflight/recovery/plan/worker/capabilities/security-report/regression/
compare-runs/benchmark。"""

from __future__ import annotations

import argparse


def configure(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    # 走查 R3.3：中文需求解析此前只有 GUI 可达；命令行的"用户要什么"这一层本该在这里。
    desire = sub.add_parser(
        "task",
        help="把一句中文需求编译成可审阅的任务设置（字段 / 范围 / 输出 / 当前不支持项）",
    )
    desire.add_argument("request", help="中文需求，例如「抓取 https://example.com 的标题和价格，输出 CSV」")
    desire.add_argument(
        "--fallback-url",
        default="",
        help="需求里没写 URL 时使用的地址",
    )
    for name, help_text in (("run", "启动或重新运行任务"), ("resume", "从中断队列继续")):
        item = sub.add_parser(name, help=help_text)
        item.add_argument("--config", "-c", required=True, help="任务配置文件路径（YAML）")
        item.add_argument("--max-pages", type=int, help="本次最多抓取多少页（覆盖配置里的上限）")
        item.add_argument("--progress", action="store_true", help="显示实时采集进度条")
        item.add_argument(
            "--strict", action="store_true",
            help="严格模式: 0 条有效记录时退出码为 1(默认向前兼容)",
        )
        if name == "resume":
            item.add_argument("--retry-failed", action="store_true", help="重试死信队列中的失败请求")
    validate = sub.add_parser("validate", help="校验配置")
    validate.add_argument("--config", "-c", required=True, help="任务配置文件路径（YAML）")
    doctor = sub.add_parser("doctor", help="检查配置、依赖和磁盘")
    doctor.add_argument("--config", "-c", required=True, help="任务配置文件路径（YAML）")
    status = sub.add_parser("status", help="查看断点库状态")
    status.add_argument("--config", "-c", required=True, help="任务配置文件路径（YAML）")
    status.add_argument("--format", default="json", choices=["json", "text"], help="输出格式 (json 用于脚本, text 人类可读)")
    export = sub.add_parser("export", help="重新导出数据库结果")
    export.add_argument("--config", "-c", required=True, help="任务配置文件路径（YAML）")
    export.add_argument("--run-id", help="要导出的运行 id（见 status；缺省导出最近一次）")
    reprocess = sub.add_parser(
        "reprocess",
        help="Re-run extraction, quality checks and export from raw archives without downloading again",
    )
    reprocess.add_argument("--config", "-c", required=True, help="任务配置文件路径（YAML）")
    reprocess.add_argument("--run-id", help="要重处理的运行 id（缺省最近一次）")
    reprocess.add_argument("--record-id", help="仅重算该记录所属归档响应，保留当前记录并生成复核候选")
    preflight = sub.add_parser("preflight", help="运行前检查依赖、磁盘、配置和资源估算")
    preflight.add_argument("--config", "-c", required=True, help="任务配置文件路径（YAML）")
    sample = sub.add_parser("sample", help="在独立工作区执行 1-10 页小样本试跑")
    sample.add_argument("--config", "-c", required=True, help="任务配置文件路径（YAML）")
    sample.add_argument("--pages", type=int, default=3, help="试跑页数（1-10，默认 3）")
    control = sub.add_parser("control", help="暂停、继续或请求安全停止正在运行的任务")
    control.add_argument("--config", "-c", required=True, help="任务配置文件路径（YAML）")
    control.add_argument(
        "action", choices=["status", "pause", "resume", "stop"],
        help="控制动作：status=查看；pause/resume=暂停/继续；stop=请求安全停止",
    )
    compare = sub.add_parser("compare-runs", help="对比两次运行的新增、删除和字段变化")
    compare.add_argument("--config", "-c", required=True, help="任务配置文件路径")
    compare.add_argument("before_run", help="基线运行 id（见 status 输出）")
    compare.add_argument("after_run", help="对比目标运行 id")
    compare.add_argument(
        "--output", "-o", help="把对比报告写到该路径（缺省只打印摘要）",
    )
    regression = sub.add_parser("regression", help="离线验证已保存的网页/API 回归样本")
    regression.add_argument("--config", "-c", required=True, help="任务配置文件路径（YAML）")
    capabilities = sub.add_parser("capabilities", help="分层检查当前任务所需的 Python、浏览器、OCR 和存储能力")
    capabilities.add_argument(
        "--mode", choices=["quick", "task", "deep"], default="quick",
        help="检查深度：quick=只查模块存在；task=按配置所需；deep=逐个导入验证",
    )
    capabilities.add_argument(
        "--require",
        action="append",
        default=[],
        metavar="FEATURE",
        help=(
            "task 模式需要的能力，可重复，名字与安装时的 extra 同名："
            "html/pdf/browser/selenium/async-http/ai/tls/streams/distributed/scrapy/"
            "ocr-tesseract/ocr-paddle/ocr-captcha/crawl4ai/gui/security/storage/"
            "postgresql/search/document（历史别名 web、streams、storage-s3 等仍可用）"
        ),
    )
    capabilities.add_argument("--verify-imports", action="store_true", help="兼容选项：实际导入全部已安装模块（较慢）")
    capabilities.add_argument("--self-test", action="store_true", help="用生成样本离线运行 Tesseract 与 PaddleOCR（分钟级）")
    capabilities.add_argument("--portable-paths", action="store_true", help="把运行路径写成可移植占位符")
    security_report = sub.add_parser("security-report", help="汇总任务实际网络访问边界和安全例外")
    security_report.add_argument("--config", "-c", required=True, help="任务配置文件路径")
    recovery = sub.add_parser("recovery", help="查看并执行任务恢复中心操作")
    recovery.add_argument("--config", "-c", required=True, help="任务配置文件路径（YAML）")
    recovery.add_argument(
        "action",
        choices=["overview", "failures", "coverage", "retry-discovery", "continue",
                 "retry-failed", "relogin", "reprocess", "rollback-config"],
        help=(
            "要执行的恢复动作：overview=总览；failures=失败清单；coverage=覆盖缺口；"
            "retry-discovery=重试发现；continue=继续；retry-failed=重试死信；"
            "relogin=重新登录；reprocess=重处理；rollback-config=回滚配置"
        ),
    )
    recovery.add_argument("--run-id", help="coverage / retry-discovery 的发现记录运行标识；默认最近运行")
    recovery.add_argument("--offset", type=int, default=0, help="coverage 清单分页偏移")
    recovery.add_argument("--limit", type=int, help="单次返回条目上限（分页用）")
    recovery.add_argument("--fingerprint", action="append", help="retry-failed 选择失败请求；retry-discovery 选择发现清单中的父请求；可重复")
    recovery.add_argument("--backup", help="rollback-config使用的已验证配置备份")
    recovery.add_argument("--apply", "--yes", action="store_true", help="执行破坏性操作（rollback-config 需要）")
    plan = sub.add_parser("plan", help="把任务编译为可解释、可校验哈希的执行计划")
    plan.add_argument("--config", "-c", required=True, help="任务配置文件路径")
    plan.add_argument("--compare", help="与另一配置的计划进行字段级差异比较")
    plan.add_argument("--output", "-o", help="把执行计划写到该路径（缺省打印到 stdout）")
    worker = sub.add_parser("worker", help="启动或重新连接认证的独立本地Worker")
    worker.add_argument("--config", "-c", required=True, help="任务配置文件路径")
    worker.add_argument(
        "action", choices=["start", "status", "pause", "resume", "stop", "shutdown"],
        help=(
            "worker 动作：start=启动/重新连接；status=查看状态；"
            "pause/resume/stop=控制运行；shutdown=关闭 worker 进程"
        ),
    )
    worker.add_argument("--session", help="会话标识（status/pause/resume/stop 时指定目标）")
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
