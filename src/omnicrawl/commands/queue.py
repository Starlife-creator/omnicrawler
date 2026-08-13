"""远程任务调度队列 CLI 后端（批 A-3）。

动作：
- submit   —— 提交配置任务到队列（Redis 可用走共享队列，否则本地降级）
- status   —— 查看后端类型、队列深度与 worker 心跳列表
- consume  —— 以 worker 身份持续消费并执行任务（Ctrl+C 优雅退出）

执行方式（--executor）：
- backend（默认）：LocalWorkerBackend.start（复用本地 worker 生命周期）
- pipeline：直接 load_config + Pipeline.run（当前进程内执行）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..runtime.redis_worker import RemoteQueue, TaskSpec, consume_loop


def _make_executor(kind: str):
    """构造单任务执行回调。"""
    if kind == "pipeline":
        from ..core.config import load_config
        from ..pipeline import Pipeline

        def _run(task: TaskSpec) -> Any:
            with Pipeline(load_config(task.config_path)) as pipeline:
                return pipeline.run()

        return _run
    from ..runtime.execution_backend import LocalWorkerBackend

    backend = LocalWorkerBackend()

    def _run(task: TaskSpec) -> Any:
        return backend.start(task.config_path)

    return _run


def execute(
    action: str,
    *,
    config: str = "",
    redis_url: str | None = None,
    local_path: str | None = None,
    worker_id: str = "",
    interval: float = 1.0,
    max_tasks: int | None = None,
    executor: str = "backend",
) -> dict[str, Any]:
    """执行队列调度动作，返回结构化结果（供 CLI _json 输出）。"""
    queue = RemoteQueue(
        redis_url=redis_url,
        local_path=Path(local_path).expanduser().resolve() if local_path else None,
    )
    try:
        if action == "status":
            return queue.status()
        if action == "submit":
            if not config:
                raise ValueError("queue submit 需要 --config <项目配置>")
            task = queue.submit(config)
            return {
                "submitted": task.to_dict(),
                "backend": queue.backend_kind(),
                "queue_size": queue.size(),
            }
        if action == "consume":
            processed = consume_loop(
                queue,
                _make_executor(executor),
                worker_id=worker_id or None,
                interval=interval,
                max_tasks=max_tasks,
                on_log=print,
            )
            return {"backend": queue.backend_kind(), "processed": processed}
        raise ValueError(f"未知队列操作: {action}")
    finally:
        queue.close()
