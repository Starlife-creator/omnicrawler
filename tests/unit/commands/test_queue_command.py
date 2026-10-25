"""C-#7：``commands.queue`` 提交 / 状态 / 消费成功失败拆分与死信台账直接单测。"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from omnicrawler.commands import queue as queue_command


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump({
        "project": {"name": "queue-cmd", "workspace": str(tmp_path / "work")},
        "source": {"kind": "crawl", "seeds": ["http://127.0.0.1:1/x"]},
        "crawl": {"max_pages": 1, "max_depth": 0, "same_host": True},
        "http": {"user_agent": "QueueCmd/1.0", "respect_robots": False, "delay_seconds": 0},
    }, sort_keys=False), encoding="utf-8")
    return path


def test_status_reports_local_backend_and_depth(tmp_path: Path) -> None:
    result = queue_command.execute("status", local_path=str(tmp_path / "q.sqlite3"))
    assert result["backend"] == "local"
    assert result["queue_size"] == 0
    assert isinstance(result["workers"], list)


def test_submit_requires_config(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="submit 需要"):
        queue_command.execute("submit", local_path=str(tmp_path / "q.sqlite3"))


def test_submit_registers_task_and_depth(tmp_path: Path) -> None:
    config = _write_config(tmp_path)
    result = queue_command.execute("submit", config=str(config), local_path=str(tmp_path / "q.sqlite3"))
    assert result["backend"] == "local"
    assert result["queue_size"] == 1
    assert result["submitted"]["config_path"].endswith("project.yaml")


def test_consume_success_splits_counts_and_empty_dead_letter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _write_config(tmp_path)
    queue_command.execute("submit", config=str(config), local_path=str(tmp_path / "q.sqlite3"))
    queue_command.execute("submit", config=str(config), local_path=str(tmp_path / "q.sqlite3"))
    monkeypatch.setattr(queue_command, "_make_executor", lambda kind, *, local_only: lambda task: None)

    result = queue_command.execute(
        "consume", local_path=str(tmp_path / "q.sqlite3"),
        max_tasks=2, interval=0.0,
    )
    assert result["processed"] == 2
    assert result["succeeded"] == 2
    assert result["failed"] == 0
    assert result["dead_letter"] == []


def test_consume_failure_records_dead_letter(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _write_config(tmp_path)
    queue_command.execute("submit", config=str(config), local_path=str(tmp_path / "q.sqlite3"))

    def _boom(task: object) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(queue_command, "_make_executor", lambda kind, *, local_only: _boom)

    result = queue_command.execute(
        "consume", local_path=str(tmp_path / "q.sqlite3"),
        max_tasks=1, interval=0.0,
    )
    assert result["processed"] == 1
    assert result["succeeded"] == 0
    assert result["failed"] == 1
    assert len(result["dead_letter"]) == 1
    assert "boom" in result["dead_letter"][0]["error"]


def test_unknown_action_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="未知队列操作"):
        queue_command.execute("bogus", local_path=str(tmp_path / "q.sqlite3"))
