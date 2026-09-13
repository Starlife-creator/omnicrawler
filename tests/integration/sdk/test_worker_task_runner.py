from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(importlib.util.find_spec("PySide6") is None, reason="PySide6 required")


class _Backend:
    def __init__(self) -> None:
        self.session = SimpleNamespace(pid=4321)
        self.next_status = {"status": "running"}
        self.calls: list[str] = []

    def start(self, _path):
        self.calls.append("start")
        return {"status": "running"}

    def attach(self, _path):
        self.calls.append("attach")
        return {"status": "running"}

    def status(self):
        self.calls.append("status")
        return self.next_status

    def pause(self):
        self.calls.append("pause")
        return {"paused": True}

    def resume(self):
        self.calls.append("resume")
        return {"paused": False}

    def stop(self):
        self.calls.append("stop")
        return {"stop_requested": True}


def test_worker_task_runner_start_control_poll_and_attach(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.core.config_model import CrawlConfig
    from omnicrawler.gui.runner.worker_task_runner import WorkerTaskRunner

    app = QApplication.instance() or QApplication([])
    runner = WorkerTaskRunner(project_root=tmp_path)
    backend = _Backend()
    runner._backend = backend
    states: list[str] = []
    finished: list[tuple[str, int]] = []
    runner.state_changed.connect(states.append)
    runner.task_finished.connect(lambda task, code: finished.append((task, code)))
    config = CrawlConfig(project_name="worker-ui", workspace=str(tmp_path / "work"), seed_urls=["https://example.org/"])
    assert runner.start(config) is True
    assert runner.is_running and runner.get_pid() == 4321 and runner.config_path.is_file()
    runner.pause()
    runner.resume()
    runner.stop()
    backend.next_status = {"status": "succeeded"}
    runner._poll()
    assert states[-1] == "finished" and finished == [(config.task_id, 0)]
    assert runner.attach(tmp_path / "worker-session.json") is True
    assert {"start", "pause", "resume", "stop", "status", "attach"} <= set(backend.calls)
    runner._poller.stop()
    app.processEvents()


def test_worker_zero_records_warns_but_still_succeeds(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.runner.worker_task_runner import WorkerTaskRunner

    app = QApplication.instance() or QApplication([])
    runner = WorkerTaskRunner(project_root=tmp_path)
    backend = _Backend()
    runner._backend = backend
    logs: list[str] = []
    finished: list[tuple[str, int]] = []
    runner.log_line.connect(lambda text, _level: logs.append(text))
    runner.task_finished.connect(lambda task, code: finished.append((task, code)))
    backend.next_status = {"status": "succeeded", "records": 0}
    runner._poll()
    assert finished == [("", 0)]
    assert any("0 条记录" in text for text in logs)
    runner._poller.stop()
    app.processEvents()


def test_worker_partial_success_is_recognized_as_finished(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.runner.worker_task_runner import WorkerTaskRunner

    app = QApplication.instance() or QApplication([])
    runner = WorkerTaskRunner(project_root=tmp_path)
    backend = _Backend()
    runner._backend = backend
    logs: list[str] = []
    finished: list[tuple[str, int]] = []
    runner.log_line.connect(lambda msg, _level: logs.append(msg))
    runner.task_finished.connect(lambda task, code: finished.append((task, code)))
    backend.next_status = {"status": "partial_success", "records": 2}
    runner._poll()
    assert finished == [("", 0)]
    assert any("部分成功" in text for text in logs)
    runner._poller.stop()
    app.processEvents()


def test_worker_cancelled_is_a_distinct_terminal_state(tmp_path: Path, monkeypatch) -> None:
    """取消必须是独立终态「已取消」，不能与失败的「错误」合并。

    历史缺陷：`_poll` 里 `cancelled` 与 `failed` 共用一个 `else` ⇒ 状态置 `error`、
    退出码 1，且没有任何提示 —— 用户**主动点「停止」**却看到「错误」。

    退出码保持 1 是**有意为之**：与 CLI 一致（`commands/run_task.py` 把 failed 与
    cancelled 都映射为 exit_code 1），不自创别的码值。
    """
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.runner.worker_task_runner import WorkerTaskRunner

    app = QApplication.instance() or QApplication([])
    runner = WorkerTaskRunner(project_root=tmp_path)
    backend = _Backend()
    runner._backend = backend
    logs: list[str] = []
    states: list[str] = []
    finished: list[tuple[str, int]] = []
    runner.log_line.connect(lambda text, _level: logs.append(text))
    runner.state_changed.connect(states.append)
    runner.task_finished.connect(lambda task, code: finished.append((task, code)))

    backend.next_status = {"status": "cancelled", "records": 3}
    runner._poll()

    assert states[-1] == "cancelled", f"应进入独立的 cancelled 终态：{states}"
    assert finished and finished[0][1] == 1, f"取消退出码应为 1：{finished}"
    assert any("已取消" in text for text in logs), f"应给用户可见提示：{logs}"
    runner._poller.stop()
    app.processEvents()
