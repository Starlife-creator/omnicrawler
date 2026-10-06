"""运维与生态域：serve / workbench / schedule / queue / scene / workspace /
components / runtime-verify。"""

from __future__ import annotations

import argparse


def configure(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    notices = sub.add_parser("notifications", help="查看记录变化通知及补发所选事件")
    notices.add_argument("--config", "-c", required=True)
    notices.add_argument("action", choices=["report", "retry"])
    notices.add_argument("--event-id", action="append", default=[], help="仅补发指定通知；可重复")
    notices.add_argument("--apply", "--yes", action="store_true", help="确认补发所选通知")
    server = sub.add_parser("serve", help="启动只读监控面板")
    server.add_argument("--config", "-c", required=True)
    server.add_argument("--host", default="127.0.0.1")
    server.add_argument("--port", type=int, default=8765)
    sub.add_parser("workbench", help="启动从采集到PDF结果的统一桌面工作台")
    # 应用自更新（issue #88）。命名用 self-update 而不是 update：配置里已有的
    # `updates` 段是**网站变更监控**，同名会把两件事混成一件。
    self_update = sub.add_parser("self-update", help="应用自更新：检查新版本 / 应用签名升级包")
    self_update_sub = self_update.add_subparsers(dest="self_update_command", required=True)
    su_check = self_update_sub.add_parser(
        "check", help="检查更新源是否有新版本（未配置信任根或更新源即报「已禁用」）"
    )
    su_check.add_argument("--config", "-c", required=True)
    su_check.add_argument("--platform", default="", help="覆盖平台键（windows/linux/macos）")
    su_check.add_argument("--edition", default="", help="覆盖版本后缀（Standard/Full）")
    su_check.add_argument("--json", dest="json_output", action="store_true", help="以 JSON 输出")
    su_apply = self_update_sub.add_parser(
        "apply", help="下载并应用签名升级包（覆盖应用文件，破坏性：需 --yes）"
    )
    su_apply.add_argument("--config", "-c", required=True)
    su_apply.add_argument("--platform", default="", help="覆盖平台键（windows/linux/macos）")
    su_apply.add_argument("--edition", default="", help="覆盖版本后缀（Standard/Full）")
    su_apply.add_argument("--package", default="", help="离线路径：直接指定本地已签名升级包")
    su_apply.add_argument("--dry-run", action="store_true", help="只输出计划，不写任何文件")
    su_apply.add_argument(
        "--full", action="store_true",
        help="跳过增量，走全量·就地替换（只占一份；本机不在增量基线内时会自动走这条）",
    )
    su_apply.add_argument(
        "--to-versions", action="store_true",
        help="全量装到 versions/<新版>/（应用根那份原样保留＝可回退；磁盘会占两份）",
    )
    su_apply.add_argument(
        "--yes", "--apply", dest="confirm", action="store_true",
        help="确认执行破坏性覆盖（与 components uninstall 同一判据）",
    )
    su_apply.add_argument("--json", dest="json_output", action="store_true", help="以 JSON 输出")
    su_ignore = self_update_sub.add_parser(
        "ignore", help="忽略当前更新源给出的版本（之后出现更新的版本会自动恢复提示）"
    )
    su_ignore.add_argument("--config", "-c", required=True)
    su_ignore.add_argument("--clear", action="store_true", help="清除已忽略的版本，恢复提示")
    su_ignore.add_argument("--json", dest="json_output", action="store_true", help="以 JSON 输出")
    su_cleanup = self_update_sub.add_parser(
        "cleanup", help="回收磁盘：清挂账残留 + 删不再使用的旧版本目录（不需要更新源）"
    )
    su_cleanup.add_argument(
        "--yes", "--apply", dest="confirm", action="store_true",
        help="确认删除（缺省只报清理计划）",
    )
    su_cleanup.add_argument("--json", dest="json_output", action="store_true", help="以 JSON 输出")
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
    workspace = sub.add_parser("workspace", help="管理项目工作区、体检、打包、快照和回滚")
    workspace.add_argument("--config", "-c", help="任务配置；import 操作无需提供")
    workspace.add_argument("action", choices=["init", "health", "package", "snapshot", "rollback", "import"])
    workspace.add_argument("--target")
    workspace.add_argument("--destination", help="工作区导入的新目录；不得覆盖已有目录")
    workspace.add_argument("--kind", choices=["full", "complete", "config", "support"], default="full", help="complete 保留历史导出，适用于搬迁")
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
