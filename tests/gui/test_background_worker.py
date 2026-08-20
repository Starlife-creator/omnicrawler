"""S3.1.1：BackgroundWorker 基类（QThread + 信号回传 + 取消/清理）。"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from omnicrawler.gui.core.background_worker import BackgroundWorker, run_worker

_app = QApplication.instance() or QApplication([])


def test_worker_emits_result_and_cleans_up() -> None:
    results: list[object] = []
    failed: list[str] = []
    finished: list[bool] = []

    class _OkWorker(BackgroundWorker):
        def work(self) -> str:
            time.sleep(0.05)
            return "done"

    worker = _OkWorker()
    worker.succeeded.connect(results.append)
    worker.failed.connect(failed.append)
    worker.finished.connect(lambda *_: finished.append(True))
    worker.start()
    assert worker.wait(5000)
    _app.processEvents()
    assert results == ["done"]
    assert failed == []
    assert finished == [True]


def test_worker_emits_failure() -> None:
    results: list[object] = []
    failed: list[str] = []

    class _BoomWorker(BackgroundWorker):
        def work(self) -> str:
            raise RuntimeError("boom")

    worker = _BoomWorker()
    worker.succeeded.connect(results.append)
    worker.failed.connect(failed.append)
    worker.start()
    assert worker.wait(5000)
    _app.processEvents()
    assert results == []
    assert failed == ["boom"]


def test_worker_cancel_suppresses_success() -> None:
    results: list[object] = []

    class _SlowWorker(BackgroundWorker):
        def work(self) -> str:
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if self.isInterruptionRequested():
                    return "ignored"
                time.sleep(0.01)
            return "finished"

    worker = _SlowWorker()
    worker.succeeded.connect(results.append)
    worker.start()
    time.sleep(0.05)
    worker.requestInterruption()
    assert worker.wait(5000)
    _app.processEvents()
    assert results == []


def test_run_worker_connects_callbacks() -> None:
    results: list[object] = []

    class _OkWorker(BackgroundWorker):
        def work(self) -> int:
            return 42

    worker = run_worker(_OkWorker(), on_succeeded=lambda value: results.append(value))
    assert worker.wait(5000)
    _app.processEvents()
    assert results == [42]


def test_worker_is_qthread_with_interruption_api() -> None:
    assert issubclass(BackgroundWorker, QThread)
