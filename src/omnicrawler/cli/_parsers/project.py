"""项目生命周期域：init / wizard / migrate / cleanup。"""

from __future__ import annotations

import argparse


def configure(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    init = sub.add_parser("init", help="复制一个可编辑的项目配置")
    init.add_argument("name")
    init.add_argument("--template", default="static_html")
    init.add_argument("--output", default="configs")
    wizard = sub.add_parser("wizard", help="交互生成基础配置")
    wizard.add_argument("--output", default="configs/new_project.yaml")
    migrate = sub.add_parser("migrate", help="把旧配置安全迁移为当前版本")
    migrate.add_argument("--config", "-c", required=True)
    migrate.add_argument("--output", "-o", required=True)
    migrate.add_argument("--force", action="store_true")
    cleanup = sub.add_parser("cleanup", help="预览或执行配置的数据保留策略")
    cleanup.add_argument("--config", "-c", required=True)
    cleanup.add_argument("--apply", action="store_true", help="实际删除；省略时只输出计划")
