"""定时任务调度命令。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.config import load_config, require_config_path
from ..runtime.scheduler import ScheduleStore

# ★ 2026-11-27：`Pipeline` 只在 `run-due` 用到，改为**用到时**才导入。
#
#   实测 `import omnicrawler.pipeline` 约 418 ms（连带 bs4 / html_tools /
#   extraction.extractors）。本模块被 `cli._handlers` 顶层导入 ⇒ 这一处 eager
#   import 会传染给**每一条 CLI 命令**，包括 `schedule list` 这种纯查询。


def _json(value: Any) -> str:
    import json
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def execute(
    action: str, *,
    database: str = "",
    name: str = "", config_path: str = "",
    every_seconds: int = 0,
    require_ac: bool = False, require_network: bool = False,
    minimum_battery: int | None = None,
    limit: int = 1,
) -> dict[str, Any]:
    db_path = Path(database).expanduser().resolve()
    with ScheduleStore(db_path) as schedules:
        if action == "add":
            schedule_id = schedules.add(
                name, Path(config_path), every_seconds,
                conditions={
                    "require_ac": require_ac,
                    "require_network": require_network,
                    "minimum_battery_percent": minimum_battery,
                },
            )
            return {"schedule_id": schedule_id}
        if action == "list":
            return {"schedules": schedules.list()}
        if action == "run-due":
            def runner(path: Path) -> dict[str, Any]:
                # B09-003：run-due 的 config_path 来自本地调度库，消费前强制校验
                # 存在且位于 CWD 内（与 queue 本地降级模式对齐，防越界加载）。
                from ..pipeline import Pipeline  # 惰性：见文件头说明

                config_path = require_config_path(path, require_inside_cwd=True)
                task_config = load_config(config_path)
                with Pipeline(task_config) as pipeline:
                    return pipeline.run()
            return {"results": schedules.run_due(runner, limit=limit)}
    raise ValueError(f"未知调度操作: {action}")
