"""A-15 不变量：``BackgroundWorker`` 每次运行**恰好发出一个终态信号**。

契约出处：``OPTIMIZATION_PLAN_2026-09`` §3.3「每次任务只发一个业务终态」；
状态路径 ``running → finished`` / ``running → failed`` / ``running → cancelling → cancelled``
（本实现中 cancelled 即 :attr:`BackgroundWorker.interrupted`）。

本测试把该契约固定下来，防止回归成「中断后不发任何信号 → 调用方永远等不到终态、
进行中状态无法复位」——即审计条目 A-15。

**Qt 语义提醒**（决定了中断测试的写法）：``QThread.requestInterruption()`` 在
「线程既未运行、也未结束」时是 **no-op**（Qt 源码：``if (!running && !finished && !isAdopted) return;``）。
因此必须先 ``start()`` 再 ``requestInterruption()``，见 :func:`test_request_interruption_before_start_is_noop`。
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QThread, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from omnicrawler.gui.core.background_worker import BackgroundWorker


@pytest.fixture(scope="module", autouse=True)
def _qapp():
    yield QApplication.instance() or QApplication([])


def _collect(worker: BackgroundWorker) -> list[str]:
    """订阅全部终态信号，返回按派发顺序记录的终态名列表。"""
    seen: list[str] = []
    worker.succeeded.connect(lambda *_: seen.append("succeeded"))
    worker.failed.connect(lambda *_: seen.append("failed"))
    worker.interrupted.connect(lambda *_: seen.append("interrupted"))
    return seen


class _Returns(BackgroundWorker):
    def work(self) -> Any:
        return 42


class _RaisesBlank(BackgroundWorker):
    def work(self) -> Any:
        raise OSError()  # str() == ""：必须回落到类型名


class _Interruptible(BackgroundWorker):
    """模拟长任务的协作取消：等到被请求中断才返回。"""

    def work(self) -> Any:
        for _ in range(600):
            if self.isInterruptionRequested():
                break
            QThread.msleep(5)
        return "payload-should-not-be-delivered"


def _start_then_interrupt(worker: BackgroundWorker) -> None:
    """启动线程、确保进入运行态后请求中断，等待结束并泵送队列信号。

    ``worker`` 的线程归属是主线程，因此它从工作线程发出的信号按**队列**方式派发；
    ``wait()`` 会阻塞主线程事件循环，必须在其后 ``qWait`` 一次才能收到终态。
    """
    worker.start()
    for _ in range(400):
        if worker.isRunning():
            break
        QThread.msleep(5)
    assert worker.isRunning(), "worker 未能进入运行态"
    worker.requestInterruption()
    assert worker.wait(5000), "worker 未在超时内结束"
    QTest.qWait(50)  # 泵送队列中的终态信号


def test_success_emits_exactly_one_terminal() -> None:
    worker = _Returns()
    seen = _collect(worker)
    worker.run()
    assert seen == ["succeeded"]


def test_failure_emits_exactly_one_terminal_with_fallback_text() -> None:
    worker = _RaisesBlank()
    seen: list[str] = _collect(worker)
    messages: list[str] = []
    worker.failed.connect(messages.append)
    worker.run()
    assert seen == ["failed"]
    assert messages == ["OSError"], "空异常消息必须回落到类型名，不能是空串"


def test_request_interruption_before_start_is_noop() -> None:
    """记录 Qt 语义：未运行/未结束的线程上 requestInterruption() 不生效。"""
    worker = _Interruptible()
    worker.requestInterruption()
    assert worker.isInterruptionRequested() is False


def test_interruption_emits_exactly_one_terminal() -> None:
    worker = _Interruptible()
    seen = _collect(worker)
    _start_then_interrupt(worker)
    assert seen == ["interrupted"], "中断后必须发出、且只发出 interrupted 终态"


def test_interruption_is_not_reported_as_failure_or_success() -> None:
    worker = _Interruptible()
    seen = _collect(worker)
    _start_then_interrupt(worker)
    assert "failed" not in seen
    assert "succeeded" not in seen


def test_emit_result_override_replaces_generic_succeeded() -> None:
    """覆写 ``_emit_result`` 的 worker（如 ``CsvIndexWorker``）只发专用信号。

    这是「统一生命周期」与「保留带类型信号」两者的兼容点：专用信号取代
    ``succeeded``，因此调用方不应同时收到两个结果信号。
    """

    class _Typed(BackgroundWorker):
        done = Signal(int)

        def work(self) -> Any:
            return 7

        def _emit_result(self, result: Any) -> None:
            self.done.emit(result)

    worker = _Typed()
    seen = _collect(worker)
    typed: list[int] = []
    worker.done.connect(typed.append)
    worker.run()
    assert typed == [7]
    assert seen == [], "覆写 _emit_result 后不应再发通用 succeeded"


class _CleanupRecorder(BackgroundWorker):
    """记录 cleanup 调用次数/线程/顺序；可选让 cleanup 抛异常。"""

    def __init__(self, *, result: Any = 42, raise_in_work: bool = False, cleanup_raises: bool = False) -> None:
        super().__init__()
        self._result = result
        self._raise_in_work = raise_in_work
        self._cleanup_raises = cleanup_raises
        self.order: list[str] = []
        self.cleanup_calls = 0
        self.cleanup_thread_ident: int | None = None

    def work(self) -> Any:
        if self._raise_in_work:
            raise OSError()
        if self._result == "wait-for-interrupt":
            for _ in range(600):
                if self.isInterruptionRequested():
                    break
                QThread.msleep(5)
        return self._result

    def _emit_result(self, result: Any) -> None:
        self.order.append("terminal")
        super()._emit_result(result)

    def cleanup(self) -> None:
        self.cleanup_calls += 1
        self.cleanup_thread_ident = threading.get_ident()
        self.order.append("cleanup")
        if self._cleanup_raises:
            raise RuntimeError("cleanup 故意失败")


def test_cleanup_runs_on_success() -> None:
    """成功路径也要收尾：钩子此前声明了但从未被调用（全历史无调用点）。"""
    worker = _CleanupRecorder()
    worker.run()  # 逻辑断言用 run()（同步、无需事件循环）
    assert worker.cleanup_calls == 1, "成功路径未调用 cleanup"


def test_cleanup_runs_on_failure() -> None:
    worker = _CleanupRecorder(raise_in_work=True)
    worker.run()
    assert worker.cleanup_calls == 1, "失败路径未调用 cleanup"


def test_cleanup_runs_on_interruption_in_the_worker_thread() -> None:
    """取消路径同样收尾，且**在工作线程内**执行（docstring 承诺的语义）。"""
    worker = _CleanupRecorder(result="wait-for-interrupt")
    _start_then_interrupt(worker)
    assert worker.cleanup_calls == 1, "取消路径未调用 cleanup"
    assert worker.cleanup_thread_ident is not None
    assert worker.cleanup_thread_ident != threading.get_ident(), (
        "cleanup 必须在工作线程内执行，而不是主线程"
    )


def test_cleanup_runs_after_the_terminal_signal() -> None:
    """顺序契约：先发终态信号，再收尾（调用方可凭终态立即复位进行中状态）。"""
    worker = _CleanupRecorder()
    worker.run()
    assert worker.order == ["terminal", "cleanup"], worker.order


def test_cleanup_failure_does_not_change_the_terminal_state() -> None:
    """清理抛异常不得改变终态、也不得让它逃出 run()（否则会盖掉真正的失败原因）。"""
    seen: list[str] = []
    worker = _CleanupRecorder(cleanup_raises=True)
    worker.succeeded.connect(lambda *_a: seen.append("succeeded"))
    worker.failed.connect(lambda *_a: seen.append("failed"))

    worker.run()  # 若清理异常逃逸，这里会直接抛错 ⇒ 用例失败

    assert seen == ["succeeded"], seen
    assert worker.cleanup_calls == 1
