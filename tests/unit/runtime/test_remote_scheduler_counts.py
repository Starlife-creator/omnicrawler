"""A-P2-6：远程队列成功 / 失败计数拆分、失败事件与死信台账回归测试。"""
from __future__ import annotations

import tempfile
from pathlib import Path

from omnicrawler.runtime.redis_worker import RemoteQueue, TaskSpec, consume_loop


def _config_file(directory: Path, name: str) -> Path:
    path = directory / name
    path.write_text("project: {name: counts}\n", encoding="utf-8")
    return path


def test_consume_loop_splits_succeeded_and_failed_counts() -> None:
    with tempfile.TemporaryDirectory() as temp:
        queue = RemoteQueue(local_path=Path(temp) / "q.sqlite3")
        try:
            queue.submit(str(_config_file(Path(temp), "ok.yaml")))
            queue.submit(str(_config_file(Path(temp), "boom.yaml")))
            events: list[dict[str, object]] = []
            stats: dict[str, int] = {}

            def executor(task: TaskSpec) -> None:
                if "boom" in task.config_path:
                    raise RuntimeError("boom")

            total = consume_loop(
                queue, executor,
                worker_id="w-counts", interval=0.01, max_tasks=2,
                on_log=lambda _msg: None, on_event=events.append, stats=stats,
            )
            assert total == 2
            assert stats == {"processed": 2, "succeeded": 1, "failed": 1}
            kinds = [event["kind"] for event in events]
            assert kinds.count("task_succeeded") == 1
            assert kinds.count("task_failed") == 1
            failed = next(event for event in events if event["kind"] == "task_failed")
            assert failed["worker_id"] == "w-counts"
            assert "RuntimeError" in str(failed["error"])
        finally:
            queue.close()


def test_failed_tasks_are_recorded_in_dead_letter() -> None:
    with tempfile.TemporaryDirectory() as temp:
        queue = RemoteQueue(local_path=Path(temp) / "q.sqlite3")
        try:
            queue.submit(str(_config_file(Path(temp), "boom.yaml")))

            def failing(_task: TaskSpec) -> None:
                raise RuntimeError("dead-letter me")

            consume_loop(
                queue, failing, worker_id="w-dl", interval=0.01, max_tasks=1,
                on_log=lambda _msg: None,
            )
            dead = queue.dead_letter()
            assert len(dead) == 1
            record = dead[0]
            assert record["worker_id"] == "w-dl"
            assert "dead-letter me" in record["error"]
            assert record["attempts"] == 1
            assert record["failed_at"]
        finally:
            queue.close()


def test_dead_letter_empty_when_all_succeed() -> None:
    with tempfile.TemporaryDirectory() as temp:
        queue = RemoteQueue(local_path=Path(temp) / "q.sqlite3")
        try:
            queue.submit(str(_config_file(Path(temp), "ok.yaml")))
            consume_loop(
                queue, lambda _task: None,
                worker_id="w-ok", interval=0.01, max_tasks=1,
                on_log=lambda _msg: None,
            )
            assert queue.dead_letter() == []
        finally:
            queue.close()
