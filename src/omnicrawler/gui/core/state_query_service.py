"""C-#13：GUI 状态库查询薄适配层（同步 / 异步迁移共用骨架）。

背景：``views/developer_inspector.py``、``views/project_dialogs.py``、
``views/reprocess_review.py`` 此前在 UI 线程直接 ``with StateStore(db) as state:``
执行查询。本模块把**不接触 Qt 控件**的查询语义收拢为纯函数，供两条路径复用：

* 当前同步调用点（迁移完成前的过渡形态）；
* 未来的 ``BackgroundWorker`` / ``SqliteQueryWorker`` 后台线程；工作线程内只允许
  调用本模块函数读取数据，结果经信号回投 UI 线程后再触碰控件（线程规则见
  ``docs/GUI_STATE_LOADING.md``）。

**当前状态：骨架就位、尚未接线。** 三个直连点位的负载测量结论与迁移触发条件
记录在架构文档；状态库规模或查询复杂度上升后，先按文档重测，再把调用点切换到
后台 worker 调用本模块。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def load_recent_runs(db_path: Path, limit: int = 50) -> list[dict[str, Any]]:
    """读取最近运行记录；数据库不存在或查询异常时返回空列表（只读检视口径）。"""
    from ...state import StateStore

    if not db_path.is_file():
        return []
    try:
        with StateStore(db_path) as state:
            return list(state.list_runs(limit))
    except Exception:  # noqa: BLE001 - 只读检视，DB 异常不阻塞界面
        return []


def load_timeline(
    db_path: Path, run_id: str
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[dict[str, Any]]]:
    """读取单次运行的记录、状态事件与阶段 checkpoint 时间线。

    Returns:
        ``(run, events, stages)``；未匹配到运行时 ``run`` 为 None。
    """
    from ...state import StateStore

    with StateStore(db_path) as state:
        run = next((r for r in state.list_runs(500) if r["run_id"] == run_id), None)
        events = state.run_events(run_id)
        stages = state.run_stages(run_id)
        return run, list(events), list(stages)


def load_run_selector_rows(db_path: Path, limit: int = 30) -> list[dict[str, Any]]:
    """读取运行对比选择器所需的 ``run_id / started_at / status`` 行。"""
    from ...state import StateStore

    if not db_path.is_file():
        return []
    with StateStore(db_path) as state:
        return list(
            state.rows(
                "SELECT run_id, started_at, status FROM runs "
                "ORDER BY started_at DESC LIMIT ?",
                (limit,),
            )
        )
