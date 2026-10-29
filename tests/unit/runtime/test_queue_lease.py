"""A-P1-1：本地 SQLite 队列在途租约 / 可见性超时 / 令牌守卫 / 退队与死信直接单测。

Redis 侧 in-flight 有序集合租约待 Redis 与多 worker 环境集成验证（代码内已标注）。
"""
from __future__ import annotations

from pathlib import Path

from omnicrawler.runtime.redis_worker import TaskSpec, _LocalQueue


def _task(task_id: str, path: Path) -> TaskSpec:
    return TaskSpec(task_id=task_id, config_path=str(path), submitted_at="2026-01-01T00:00:00Z", source="cli")


def test_atomic_claim_prevents_double_dispatch(tmp_path: Path) -> None:
    path = tmp_path / "queue.sqlite3"
    q1, q2 = _LocalQueue(path), _LocalQueue(path)
    try:
        q1.submit(_task("t1", tmp_path / "ok.yaml"))
        first = q1.pop("w1")
        second = q2.pop("w2")
        assert first is not None and first.task_id == "t1"
        assert second is None
    finally:
        q1.close()
        q2.close()


def test_expired_lease_is_reclaimed_with_attempt_increment(tmp_path: Path) -> None:
    queue = _LocalQueue(tmp_path / "queue.sqlite3")
    try:
        queue.submit(_task("t1", tmp_path / "ok.yaml"))
        first = queue.pop("w1", lease_seconds=0.0)
        assert first is not None
        # 人工把可见性时间戳置为过去，模拟 worker 崩溃未 ack。
        queue.conn.execute("UPDATE tasks SET visible_at=0 WHERE task_id=?", ("t1",))
        second = queue.pop("w2")
        assert second is not None
        assert second.task_id == "t1"
        assert second.attempts == 2
    finally:
        queue.close()


def test_ack_requires_matching_lease_token(tmp_path: Path) -> None:
    queue = _LocalQueue(tmp_path / "queue.sqlite3")
    try:
        queue.submit(_task("t1", tmp_path / "ok.yaml"))
        task = queue.pop("w1")
        assert task is not None
        queue.ack(task.task_id, "wrong-token")
        assert queue.size() == 1
        queue.ack(task.task_id, task.lease_token)
        assert queue.size() == 0
    finally:
        queue.close()


def test_nack_requeues_until_max_attempts_then_dead_letters(tmp_path: Path) -> None:
    queue = _LocalQueue(tmp_path / "queue.sqlite3")
    config = tmp_path / "ok.yaml"
    config.write_text("project: {name: x}\n", encoding="utf-8")
    try:
        queue.submit(_task("t1", config))
        first = queue.pop("w1")
        assert first is not None
        queue.nack(first, first.lease_token, "boom", "w1", max_attempts=3)

        second = queue.pop("w2")
        assert second is not None and second.attempts == 2
        queue.nack(second, second.lease_token, "boom", "w2", max_attempts=3)

        third = queue.pop("w3")
        assert third is not None and third.attempts == 3
        queue.nack(third, third.lease_token, "boom", "w3", max_attempts=3)

        assert queue.size() == 0
        dead = queue.dead_letter()
        assert len(dead) == 1
        assert dead[0]["task_id"] == "t1"
        assert "boom" in dead[0]["error"]
    finally:
        queue.close()
