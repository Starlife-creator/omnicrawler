"""A-15 消费方契约：``interrupted`` 终态必须真的复位进行中状态，且**只处理当前任务**。

基类新增 ``interrupted`` 只是手段；真正闭环 A-15 的是消费方把「加载中」态复位。
本文件用 ``ChartView`` 作为代表性消费方，验证两个分支：

1. **当前任务**被取消 → 复位加载态（进度条隐藏、文案提示已取消）；
2. **旧任务迟到**的取消信号 → 必须被忽略（否则会把新任务的加载态误复位）。
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from omnicrawler.gui.core.background_worker import BackgroundWorker
from omnicrawler.gui.views.chart_view import ChartView


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    yield QApplication.instance() or QApplication([])


class _Interruptible(BackgroundWorker):
    def work(self) -> Any:
        for _ in range(600):
            if self.isInterruptionRequested():
                break
            QThread.msleep(5)
        return "payload-should-not-be-delivered"


def _start_then_interrupt(worker: BackgroundWorker) -> None:
    worker.start()
    for _ in range(400):
        if worker.isRunning():
            break
        QThread.msleep(5)
    assert worker.isRunning(), "worker 未能进入运行态"
    worker.requestInterruption()
    assert worker.wait(5000), "worker 未在超时内结束"
    QTest.qWait(50)  # 泵送队列中的终态信号


def test_current_task_interruption_resets_loading_state() -> None:
    view = ChartView()
    view._loading_bar.setVisible(True)
    # 注意：ChartView 未 show()，故 isVisible() 恒为 False；
    # 判定「显式可见性」必须用 isVisibleTo(父窗口)。
    assert view._loading_bar.isVisibleTo(view) is True, "前置条件：加载态应已置为可见"

    worker = _Interruptible()
    view._worker = worker  # 伪装成「当前任务」（与 load_csv 的接线一致）
    worker.interrupted.connect(view._on_interrupted)

    _start_then_interrupt(worker)

    assert view._loading_bar.isVisibleTo(view) is False, "取消后必须隐藏加载进度条"
    assert "取消" in view._summary.text()


def test_stale_task_interruption_is_ignored() -> None:
    """旧任务迟到的取消信号不得复位新任务的加载态。"""
    view = ChartView()
    view._loading_bar.setVisible(True)
    assert view._loading_bar.isVisibleTo(view) is True

    stale = _Interruptible()
    current = _Interruptible()  # 已被新任务取代的「当前任务」
    view._worker = current

    stale.interrupted.connect(view._on_interrupted)
    _start_then_interrupt(stale)

    assert view._loading_bar.isVisibleTo(view) is True, "旧任务的取消信号不应改动当前任务的状态"
