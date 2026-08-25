"""任务执行域：run/resume/validate/doctor/status/export/reprocess/sample/
control/preflight/recovery/plan/worker/capabilities/security-report/regression/
compare-runs/benchmark。"""

from __future__ import annotations

import argparse


def configure(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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
