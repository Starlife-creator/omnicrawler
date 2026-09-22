"""插件域：plugins（list / audit / scaffold-contract2 / review-analyze）。"""

from __future__ import annotations

import argparse


def configure(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    plugins = sub.add_parser("plugins", help="列出已注册插件 / 插件作者旅程（自检、脚手架）")
    plugins.add_argument("--config", "-c")
    # ★ W3.5（2026-09-16）：此处必须**枚举全部**确实存在的子命令 —— 作者是照着
    #   `docs/AUTHOR_GUIDE.md` 第 1 步敲 `scaffold-contract2` 的，而此前这条 help 只写
    #   "子命令：audit"，作者读 help 会以为脚手架命令不存在（同一份 help 里却列着它的
    #   `--plugin-id` 参数，自相矛盾）。守卫：`tests/unit/cli/test_plugin_author_journey_w35.py`。
    plugins.add_argument(
        "plugins_command",
        nargs="?",
        default=None,
        help="子命令：audit | scaffold-contract2（省略则列出已注册插件）",
    )
    # Phase 1（B5）：plugins audit --local <dir> 本地自检（许可+凭据，与 CI 门 2 同逻辑）
    plugins.add_argument("--local", default=None, help="audit 子命令：审计的本地插件目录")
    # Phase 2a（B5/H4）：plugins audit --report 生成脱敏环境诊断报告
    plugins.add_argument("--report", action="store_true", help="audit 子命令：生成脱敏环境诊断报告")
    # P2-2：--report 默认是给人看的文本；给机器消费时显式选 json（stdout 口径才一致）
    plugins.add_argument(
        "--format", default="text", choices=("text", "json"),
        help="audit 子命令：--report 的 stdout 形态（默认 text；json 供机器消费）",
    )
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
