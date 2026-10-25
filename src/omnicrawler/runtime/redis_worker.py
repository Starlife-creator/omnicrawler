"""远程任务调度（降级版）（批 A-3）。

设计决策（A-3）：
- **降级策略**：Redis 可用（redis 包已装且能 ping 通）→ RedisFrontier 共享队列，
  跨 worker 分发；Redis 不可用（未安装 / 连接失败）→ 自动降级为本地 SQLite 队列
  （标准库实现，零外部依赖），调度接口一致，调用方无感。
- **register/heartbeat**：worker 在 ``namespace:workers``（Redis hash 或 SQLite 表）
  维护心跳与状态；心跳超时（WORKER_TTL）视为失联，由 status 过滤。
- **consume_loop**：持续 pop → 执行（executor 回调）→ 心跳续期；
  空队列按 interval 休眠轮询；Ctrl+C / max_tasks 优雅退出。
- **提交内容**：任务描述 = {task_id, config_path, submitted_at, source}。

本模块不直接调用爬虫：执行动作由调用方通过 executor 注入（CLI 提供
backend / pipeline 两种默认实现），便于测试与替换。
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC
from pathlib import Path
from typing import Any, cast

from ..core.utils import utcnow

#: 心跳过期阈值（秒）：超过此时间无心跳的 worker 视为失联
WORKER_TTL_SECONDS = 120
#: Redis 探测连接超时（秒）
_REDIS_PROBE_TIMEOUT = 1.0


@dataclass(slots=True)
class TaskSpec:
    """一条远程任务描述（队列载荷）。"""

    task_id: str
    config_path: str
    submitted_at: str
    source: str = "cli"

    def to_dict(self) -> dict[str, str]:
        return dict(asdict(self))


def _default_worker_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


# ── 本地降级后端（SQLite，零外部依赖）────────────────────────────


class _LocalQueue:
    """单机降级队列：tasks + workers 两张表，FIFO 按提交时间出队。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, timeout=10)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=10000")
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks(
                task_id TEXT PRIMARY KEY,
                config_path TEXT NOT NULL,
                submitted_at TEXT NOT NULL,
                source TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS workers(
                worker_id TEXT PRIMARY KEY,
                heartbeat_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'idle'
            );
            CREATE TABLE IF NOT EXISTS dead_letter(
                task_id TEXT PRIMARY KEY,
                config_path TEXT NOT NULL,
                submitted_at TEXT NOT NULL,
                source TEXT NOT NULL,
                error TEXT NOT NULL,
                worker_id TEXT NOT NULL,
                failed_at TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 1
            );
            """
        )

    def close(self) -> None:
        self.conn.close()

    # 队列
    def submit(self, task: TaskSpec) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO tasks(task_id, config_path, submitted_at, source) VALUES(?,?,?,?)",
                (task.task_id, task.config_path, task.submitted_at, task.source),
            )

    def pop(self) -> TaskSpec | None:
        """原子弹出最早任务。

        FINAL-R2：SELECT 后用条件 DELETE 并校验 rowcount——deferred 事务下两个
        worker 可同时读到同一行，后提交者 DELETE 影响 0 行却仍返回 TaskSpec，
        造成同一任务重复派发。rowcount!=1 时循环取下一条。
        """
        while True:
            with self.conn:
                row = self.conn.execute(
                    "SELECT * FROM tasks ORDER BY submitted_at ASC, rowid ASC LIMIT 1"
                ).fetchone()
                if row is None:
                    return None
                cursor = self.conn.execute(
                    "DELETE FROM tasks WHERE task_id=?", (row["task_id"],)
                )
                if cursor.rowcount == 1:
                    return TaskSpec(
                        task_id=row["task_id"],
                        config_path=row["config_path"],
                        submitted_at=row["submitted_at"],
                        source=row["source"],
                    )
            # 该行被其他进程抢先消费，继续取下一条

    def size(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()
        return int(row["n"])

    # worker 注册 / 心跳
    def register_worker(self, worker_id: str) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO workers(worker_id, heartbeat_at, status) VALUES(?,?,?)",
                (worker_id, utcnow(), "idle"),
            )

    def heartbeat(self, worker_id: str, *, status: str = "idle") -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO workers(worker_id, heartbeat_at, status) VALUES(?,?,?)",
                (worker_id, utcnow(), status),
            )

    def unregister_worker(self, worker_id: str) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM workers WHERE worker_id=?", (worker_id,))

    # ── A-P2-6：死信台账（失败任务留痕，便于排查与人工重放） ──
    def dead_letter_record(self, task: TaskSpec, error: str, worker_id: str) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO dead_letter(task_id, config_path, submitted_at, source,
                                        error, worker_id, failed_at, attempts)
                VALUES(?,?,?,?,?,?,?,1)
                ON CONFLICT(task_id) DO UPDATE SET
                    error=excluded.error, worker_id=excluded.worker_id,
                    failed_at=excluded.failed_at, attempts=attempts+1
                """,
                (task.task_id, task.config_path, task.submitted_at, task.source,
                 error[:4000], worker_id, utcnow()),
            )

    def dead_letter(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM dead_letter ORDER BY failed_at ASC"
        ).fetchall()
        return [dict(row) for row in rows]

    def list_workers(self, *, now: str | None = None) -> list[dict[str, Any]]:
        rows = self.conn.execute("SELECT * FROM workers").fetchall()
        cutoff = _stale_cutoff(now)
        workers: list[dict[str, Any]] = []
        for row in rows:
            stale = row["heartbeat_at"] < cutoff
            workers.append({
                "worker_id": row["worker_id"],
                "heartbeat_at": row["heartbeat_at"],
                "status": row["status"],
                "alive": not stale,
            })
        return workers


# ── Redis 后端（可选依赖，RedisFrontier 之上加任务描述与 worker 注册）────


class _RedisQueue:
    """跨 worker 共享队列：zset 任务序号 + hash 载荷 + workers hash 心跳。"""

    _WORKERS_KEY = "workers"
    _TASK_KEY = "taskqueue"
    _PAYLOAD_KEY = "taskpayload"
    _DEADLETTER_KEY = "deadletter"

    def __init__(self, redis_url: str, namespace: str = "omnicrawler") -> None:
        from .redis_frontier import RedisFrontier

        self.frontier = RedisFrontier(redis_url, namespace=namespace)
        self.client = self.frontier.client
        self.namespace = namespace

    @property
    def _queue_key(self) -> str:
        return f"{self.namespace}:{self._TASK_KEY}"

    @property
    def _payload_key(self) -> str:
        return f"{self.namespace}:{self._PAYLOAD_KEY}"

    @property
    def _workers_key(self) -> str:
        return f"{self.namespace}:{self._WORKERS_KEY}"

    def submit(self, task: TaskSpec) -> None:
        pipe = self.client.pipeline(transaction=True)
        pipe.zadd(self._queue_key, {task.task_id: time.time()})
        pipe.hset(self._payload_key, task.task_id, json.dumps(task.to_dict(), ensure_ascii=False))
        pipe.execute()

    def pop(self) -> TaskSpec | None:
        rows = cast(list[tuple[str, float]], self.client.zpopmin(self._queue_key, 1))
        if not rows:
            return None
        task_id = rows[0][0]
        raw = cast(str | None, self.client.hget(self._payload_key, task_id))
        self.client.hdel(self._payload_key, task_id)
        if raw is None:
            return None
        return TaskSpec(**json.loads(raw))

    def size(self) -> int:
        return int(cast(int, self.client.zcard(self._queue_key)))

    def register_worker(self, worker_id: str) -> None:
        self.client.hset(
            self._workers_key, worker_id,
            json.dumps({"heartbeat_at": utcnow(), "status": "idle"}, ensure_ascii=False),
        )
        self.client.expire(self._workers_key, WORKER_TTL_SECONDS)

    def heartbeat(self, worker_id: str, *, status: str = "idle") -> None:
        self.client.hset(
            self._workers_key, worker_id,
            json.dumps({"heartbeat_at": utcnow(), "status": status}, ensure_ascii=False),
        )
        self.client.expire(self._workers_key, WORKER_TTL_SECONDS)

    def unregister_worker(self, worker_id: str) -> None:
        self.client.hdel(self._workers_key, worker_id)

    # ── A-P2-6：死信台账（Redis hash + zset 序号，口径与本地队列一致） ──
    def dead_letter_record(self, task: TaskSpec, error: str, worker_id: str) -> None:
        previous = cast(str | None, self.client.hget(self._deadletter_key, task.task_id))
        attempts = 1
        if previous is not None:
            try:
                attempts = int(json.loads(previous).get("attempts", 1)) + 1
            except (json.JSONDecodeError, TypeError, ValueError):
                attempts = 1
        record = {
            "task_id": task.task_id,
            "config_path": task.config_path,
            "submitted_at": task.submitted_at,
            "source": task.source,
            "error": error[:4000],
            "worker_id": worker_id,
            "failed_at": utcnow(),
            "attempts": attempts,
        }
        pipe = self.client.pipeline(transaction=True)
        pipe.hset(self._deadletter_key, task.task_id, json.dumps(record, ensure_ascii=False))
        pipe.zadd(self._deadletter_index, {task.task_id: time.time()})
        pipe.execute()

    def dead_letter(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for task_id in cast(list[str], self.client.zrange(self._deadletter_index, 0, -1)):
            raw = cast(str | None, self.client.hget(self._deadletter_key, task_id))
            if raw is not None:
                records.append(json.loads(raw))
        return records

    @property
    def _deadletter_key(self) -> str:
        return f"{self.namespace}:{self._DEADLETTER_KEY}"

    @property
    def _deadletter_index(self) -> str:
        return f"{self.namespace}:{self._DEADLETTER_KEY}:index"

    def list_workers(self, *, now: str | None = None) -> list[dict[str, Any]]:
        cutoff = _stale_cutoff(now)
        workers: list[dict[str, Any]] = []
        raw = cast(dict[Any, Any], self.client.hgetall(self._workers_key)) or {}
        for worker_id, payload in raw.items():
            try:
                info = json.loads(payload)
            except json.JSONDecodeError:
                continue
            heartbeat_at = str(info.get("heartbeat_at", ""))
            workers.append({
                "worker_id": str(worker_id),
                "heartbeat_at": heartbeat_at,
                "status": str(info.get("status", "idle")),
                "alive": heartbeat_at >= cutoff,
            })
        return workers

    def close(self) -> None:
        """关闭 Redis 连接（与 _LocalQueue.close 接口对齐）。"""
        self.client.close()


def _stale_cutoff(now: str | None) -> str:
    """失联判定基准时间：now（ISO）或当前时间减去 WORKER_TTL。"""
    from datetime import datetime, timedelta

    if now:
        return now
    return (datetime.now(UTC) - timedelta(seconds=WORKER_TTL_SECONDS)).isoformat()


# ── 门面与探测 ────────────────────────────────────────────────


def _probe_redis(redis_url: str | None) -> bool:
    """Redis 是否可用：包已装且能 ping 通（限时探测，失败即降级）。"""
    if not redis_url:
        return False
    try:
        import redis

        client = redis.Redis.from_url(
            redis_url,
            socket_connect_timeout=_REDIS_PROBE_TIMEOUT,
            socket_timeout=_REDIS_PROBE_TIMEOUT,
        )
        return bool(client.ping())
    except Exception:  # noqa: BLE001 —— 探测失败一律降级本地队列
        return False


class RemoteQueue:
    """远程任务队列门面：自动选择 Redis（可用）或本地 SQLite（降级）。"""

    def __init__(
        self,
        *,
        redis_url: str | None = None,
        local_path: Path | None = None,
        namespace: str = "omnicrawler",
    ) -> None:
        if _probe_redis(redis_url):
            self._backend: _RedisQueue | _LocalQueue = _RedisQueue(redis_url or "", namespace=namespace)
            self._kind = "redis"
            self.local_path: Path | None = None
        else:
            path = local_path or Path.cwd() / ".omnicrawler" / "queue.sqlite3"
            self._backend = _LocalQueue(path)
            self._kind = "local"
            self.local_path = path

    def backend_kind(self) -> str:
        """当前后端：redis（共享队列）或 local（降级队列）。"""
        return self._kind

    def submit(self, config_path: str, *, source: str = "cli") -> TaskSpec:
        """提交一个配置任务；config 文件不存在抛 FileNotFoundError。"""
        path = Path(config_path).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"远程调度: 任务配置文件不存在: {path}")
        task = TaskSpec(
            task_id=uuid.uuid4().hex,
            config_path=str(path),
            submitted_at=utcnow(),
            source=source,
        )
        self._backend.submit(task)
        return task

    def pop(self) -> TaskSpec | None:
        return self._backend.pop()

    def size(self) -> int:
        return self._backend.size()

    def register_worker(self, worker_id: str | None = None) -> str:
        worker_id = worker_id or _default_worker_id()
        self._backend.register_worker(worker_id)
        return worker_id

    def heartbeat(self, worker_id: str, *, status: str = "idle") -> None:
        self._backend.heartbeat(worker_id, status=status)

    def unregister_worker(self, worker_id: str) -> None:
        self._backend.unregister_worker(worker_id)

    def list_workers(self) -> list[dict[str, Any]]:
        return self._backend.list_workers()

    def dead_letter_record(self, task: TaskSpec, error: str, worker_id: str) -> None:
        """A-P2-6：把失败任务写入后端死信台账（门面透传，Redis / SQLite 口径一致）。"""
        self._backend.dead_letter_record(task, error, worker_id)

    def dead_letter(self) -> list[dict[str, Any]]:
        """A-P2-6：失败任务死信台账（成功 / 失败计数拆分后的失败侧证据）。"""
        return self._backend.dead_letter()

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.backend_kind(),
            "queue_size": self.size(),
            "workers": self.list_workers(),
        }

    def close(self) -> None:
        self._backend.close()


# ── 消费循环 ──────────────────────────────────────────────────


def consume_loop(
    queue: RemoteQueue,
    executor: Callable[[TaskSpec], Any],
    *,
    worker_id: str | None = None,
    interval: float = 1.0,
    max_tasks: int | None = None,
    on_log: Callable[[str], None] = print,
    on_event: Callable[[dict[str, Any]], None] | None = None,
    stats: dict[str, int] | None = None,
) -> int:
    """持续消费队列任务并执行。

    A-P2-6：成功 / 失败计数拆分；失败任务落死信台账并触发 ``task_failed`` 事件；
    成功任务触发 ``task_succeeded`` 事件。

    Args:
        queue: 远程队列门面。
        executor: 单任务执行回调（返回任意值，结果忽略）。
        worker_id: worker 标识；None 自动生成（hostname-pid）。
        interval: 空队列时的轮询间隔（秒）。
        max_tasks: 最多执行任务数；None 表示无限（Ctrl+C 中断）。
        on_log: 日志输出回调。
        on_event: 任务结果事件回调（成功 / 失败）；回调自身异常不拖垮消费循环。
        stats: 可选字典，回填 ``processed`` / ``succeeded`` / ``failed`` 拆分计数。

    Returns:
        实际执行任务总数（succeeded + failed）。
    """
    worker_id = queue.register_worker(worker_id)
    processed = 0
    succeeded = 0
    failed = 0
    if stats is None:
        stats = {}

    def _emit(event: dict[str, Any]) -> None:
        if on_event is None:
            return
        try:
            on_event(event)
        except Exception:  # noqa: BLE001 —— 事件回调异常不得中断消费循环
            pass

    try:
        while max_tasks is None or processed < max_tasks:
            task = queue.pop()
            if task is None:
                if max_tasks is not None and processed >= max_tasks:
                    break
                time.sleep(max(0.05, interval))
                queue.heartbeat(worker_id, status="idle")
                continue
            queue.heartbeat(worker_id, status="busy")
            on_log(f"[worker:{worker_id}] 执行任务 {task.task_id} <- {task.config_path}")
            try:
                executor(task)
            except Exception as exc:  # noqa: BLE001 —— 单任务失败不拖垮消费循环
                failed += 1
                error = f"{type(exc).__name__}: {exc}"
                on_log(f"[worker:{worker_id}] 任务 {task.task_id} 失败: {error}")
                queue.dead_letter_record(task, error, worker_id)
                _emit({
                    "kind": "task_failed",
                    "task_id": task.task_id,
                    "config_path": task.config_path,
                    "worker_id": worker_id,
                    "error": error,
                    "timestamp": utcnow(),
                })
            else:
                succeeded += 1
                _emit({
                    "kind": "task_succeeded",
                    "task_id": task.task_id,
                    "worker_id": worker_id,
                    "timestamp": utcnow(),
                })
            processed += 1
    except KeyboardInterrupt:
        on_log(f"[worker:{worker_id}] 收到中断，已消费 {processed} 个任务")
    finally:
        queue.unregister_worker(worker_id)
    stats["processed"] = processed
    stats["succeeded"] = succeeded
    stats["failed"] = failed
    return processed


__all__ = [
    "RemoteQueue",
    "TaskSpec",
    "WORKER_TTL_SECONDS",
    "consume_loop",
    "default_worker_id",
]


def default_worker_id() -> str:
    """公开别名：生成默认 worker 标识。"""
    return _default_worker_id()
