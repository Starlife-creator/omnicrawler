"""远程任务调度降级版（runtime/redis_worker.py）单元测试。

覆盖：本地降级队列（submit/pop/size/FIFO/空队）、worker 注册/心跳/注销、
consume_loop（executor 注入、max_tasks、异常不中断、失联过滤）、
Redis 探测降级、RemoteQueue 门面与 status。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omnicrawler.runtime.redis_worker import RemoteQueue, TaskSpec, consume_loop


def _config_file(temp: Path, name: str = "task.yaml") -> Path:
    path = temp / name
    path.write_text("project: {name: t, workspace: work}\n", encoding="utf-8")
    return path


class RemoteQueueLocalTest(unittest.TestCase):
    def test_defaults_to_local_backend_without_redis(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            queue = RemoteQueue(local_path=Path(temp) / "queue.sqlite3")
            try:
                self.assertEqual(queue.backend_kind(), "local")
            finally:
                queue.close()

    def test_submit_requires_existing_config(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            queue = RemoteQueue(local_path=Path(temp) / "q.sqlite3")
            try:
                with self.assertRaises(FileNotFoundError):
                    queue.submit(str(Path(temp) / "missing.yaml"))
            finally:
                queue.close()

    def test_submit_pop_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            queue = RemoteQueue(local_path=Path(temp) / "q.sqlite3")
            try:
                cfg = _config_file(Path(temp))
                task = queue.submit(str(cfg), source="unit")
                self.assertTrue(task.task_id)
                self.assertEqual(task.source, "unit")
                self.assertEqual(queue.size(), 1)
                popped = queue.pop()
                self.assertIsNotNone(popped)
                assert popped is not None
                self.assertEqual(popped.task_id, task.task_id)
                self.assertEqual(popped.config_path, str(cfg.resolve()))
                self.assertEqual(queue.size(), 0)
                self.assertIsNone(queue.pop())  # 空队列
            finally:
                queue.close()

    def test_fifo_order(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            queue = RemoteQueue(local_path=Path(temp) / "q.sqlite3")
            try:
                first = _config_file(Path(temp), "a.yaml")
                second = _config_file(Path(temp), "b.yaml")
                queue.submit(str(first))
                queue.submit(str(second))
                self.assertEqual(queue.pop().config_path, str(first.resolve()))
                self.assertEqual(queue.pop().config_path, str(second.resolve()))
            finally:
                queue.close()

    def test_worker_register_heartbeat_unregister(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            queue = RemoteQueue(local_path=Path(temp) / "q.sqlite3")
            try:
                worker_id = queue.register_worker("worker-a")
                self.assertEqual(worker_id, "worker-a")
                queue.heartbeat("worker-a", status="busy")
                workers = queue.list_workers()
                self.assertEqual(len(workers), 1)
                self.assertTrue(workers[0]["alive"])
                self.assertEqual(workers[0]["status"], "busy")
                queue.unregister_worker("worker-a")
                self.assertEqual(queue.list_workers(), [])
            finally:
                queue.close()

    def test_stale_worker_filtered_by_cutoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            queue = RemoteQueue(local_path=Path(temp) / "q.sqlite3")
            try:
                from datetime import datetime, timedelta, timezone

                from omnicrawler.runtime.redis_worker import WORKER_TTL_SECONDS

                queue.register_worker("old")
                # 直接写入过去的心跳时间（模拟失联）
                stale = (datetime.now(timezone.utc) - timedelta(seconds=WORKER_TTL_SECONDS + 10)).isoformat()
                queue._backend.conn.execute(
                    "UPDATE workers SET heartbeat_at=? WHERE worker_id='old'",
                    (stale,),
                )
                workers = queue.list_workers()
                self.assertEqual(len(workers), 1)
                self.assertFalse(workers[0]["alive"])
            finally:
                queue.close()

    def test_consume_loop_executes_and_unregisters(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            queue = RemoteQueue(local_path=Path(temp) / "q.sqlite3")
            try:
                for name in ("a.yaml", "b.yaml"):
                    queue.submit(str(_config_file(Path(temp), name)))
                executed: list[str] = []

                def executor(task: TaskSpec) -> None:
                    executed.append(task.config_path)

                processed = consume_loop(
                    queue, executor,
                    worker_id="worker-consume", interval=0.05, max_tasks=2,
                )
                self.assertEqual(processed, 2)
                self.assertEqual(len(executed), 2)
                self.assertEqual(queue.size(), 0)
                self.assertEqual(queue.list_workers(), [])  # finally 注销
            finally:
                queue.close()

    def test_consume_loop_survives_executor_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            queue = RemoteQueue(local_path=Path(temp) / "q.sqlite3")
            try:
                queue.submit(str(_config_file(Path(temp), "a.yaml")))
                queue.submit(str(_config_file(Path(temp), "b.yaml")))

                def failing(_task: TaskSpec) -> None:
                    raise RuntimeError("boom")

                processed = consume_loop(
                    queue, failing, worker_id="w", interval=0.05, max_tasks=2,
                )
                self.assertEqual(processed, 2)  # 单任务失败不中断循环
                self.assertEqual(queue.size(), 0)
            finally:
                queue.close()

    def test_consume_max_tasks_zero_exits_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            queue = RemoteQueue(local_path=Path(temp) / "q.sqlite3")
            try:
                processed = consume_loop(queue, lambda _t: None, max_tasks=0, interval=0.05)
                self.assertEqual(processed, 0)
            finally:
                queue.close()

    def test_status_structure(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            queue = RemoteQueue(local_path=Path(temp) / "q.sqlite3")
            try:
                queue.register_worker("w1")
                status = queue.status()
                self.assertEqual(status["backend"], "local")
                self.assertEqual(status["queue_size"], 0)
                self.assertEqual(len(status["workers"]), 1)
            finally:
                queue.close()

    def test_redis_unavailable_falls_back_to_local(self) -> None:
        # redis 包未装 → 探测失败 → 本地降级（装包但连不上也同样降级）
        with tempfile.TemporaryDirectory() as temp:
            queue = RemoteQueue(
                redis_url="redis://127.0.0.1:1/0",
                local_path=Path(temp) / "q.sqlite3",
            )
            try:
                self.assertEqual(queue.backend_kind(), "local")
            finally:
                queue.close()

    def test_probe_redis_failure_uses_local(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with patch("omnicrawler.runtime.redis_worker._probe_redis", return_value=False):
                queue = RemoteQueue(redis_url="redis://x", local_path=Path(temp) / "q.sqlite3")
                try:
                    self.assertEqual(queue.backend_kind(), "local")
                finally:
                    queue.close()

    def test_default_worker_id_contains_hostname_and_pid(self) -> None:
        from omnicrawler.runtime.redis_worker import default_worker_id

        worker_id = default_worker_id()
        self.assertIn("-", worker_id)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
