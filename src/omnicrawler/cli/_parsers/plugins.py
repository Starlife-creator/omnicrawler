"""插件域：plugins（list / audit / scaffold-contract2 / review-analyze）。"""

from __future__ import annotations

import argparse


def configure(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
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
    # Phase 3（Q4/G3）：plugins review-analyze --local <file> 审核辅助分析
    plugins.add_argument(
        "--review", default=None, metavar="FILE",
        help="review-analyze 子命令：对插件文件做静态审核辅助分析（AI 增强审核员）",
    )
