"""BackgroundWorker 基类（S3.1.1）。

统一的后台任务基类：QThread + 结果/失败信号 + 取消 + 自动清理。
GUI 阻塞点（环境检测、pip 安装、导出、扫描、渲染、预检）统一接入，
保证点击后界面保持可交互。

「自动清理」＝ :meth:`BackgroundWorker.cleanup` 由 :meth:`run` 的 ``finally`` 调用，
**成功 / 失败 / 取消三条路径都会执行**（2026-09-14 接线；此前该钩子声明了但从未被调用）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from ..i18n import _

LOGGER = logging.getLogger(__name__)


class BackgroundWorker(QThread):
    """后台工作线程基类。

    子类实现 :meth:`work`（在工作线程执行），结果经 :attr:`succeeded`
    回传主线程；异常经 :attr:`failed` 回传。任务取消（requestInterruption）
    后不发射成功信号，改为发射 :attr:`interrupted`。

    **终态契约（每次运行恰好一个终态）**：``succeeded``（或子类覆写
    :meth:`_emit_result` 后发出的专用信号）／ ``failed`` ／ ``interrupted`` 三者
    **互斥且必居其一**，对应 ``OPTIMIZATION_PLAN_2026-09`` §3.3 的状态路径
    ``running → finished`` / ``running → failed`` / ``running → cancelling → cancelled``。
    调用方应把 ``interrupted`` 当作**正常终态**处理（复位进行中状态），而非错误。
    """

    succeeded = Signal(object)
    failed = Signal(str)

    #: 因 ``requestInterruption`` 终止、未产生结果时的终态信号。
    interrupted = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)

    def run(self) -> None:
        try:
            try:
                result = self.work()
            except Exception as exc:  # noqa: BLE001 - 跨线程边界统一收口
                # 异常可能没有消息（如 OSError()/ValueError() 的 str() 为空串），
                # 回落到类型名，避免界面出现空白错误提示。
                self.failed.emit(str(exc).strip() or type(exc).__name__)
                return
            if self.isInterruptionRequested():
                # 取消是正常终态，不是失败：显式发出 interrupted，
                # 保证调用方总能收到「恰好一个」终态信号并复位进行中状态。
                self.interrupted.emit()
                return
            self._emit_result(result)
        finally:
            # 统一生命周期：成功 / 失败 / 取消三条路径都在工作线程内收尾。
            # 清理失败**不得**改变任务终态（终态信号已在上方发出），只记日志——
            # 否则一个清理异常会盖掉真正的失败原因。
            try:
                self.cleanup()
            except Exception:  # noqa: BLE001 - 清理异常不改变终态
                LOGGER.exception(_("后台任务清理失败（终态已发出，不受影响）"))

    def _emit_result(self, result: Any) -> None:
        """结果回传钩子。

        默认发出通用 :attr:`succeeded`；子类若需要保留**带类型的专用信号**
        （如 ``finished_indexing = Signal(list, int, float)``），覆写本方法，
        即可在复用统一生命周期（取消 / 失败 / 清理）的同时不丢失信号类型信息。
        """
        self.succeeded.emit(result)

    def work(self) -> Any:
        """在工作线程中执行的阻塞任务；子类必须实现。"""
        raise NotImplementedError

    def cleanup(self) -> None:
        """任务结束后的资源清理（**工作线程内调用**）。

        由 :meth:`run` 的 ``finally`` 调用，因此**成功 / 失败 / 取消三条路径都会执行**，
        且晚于终态信号发出。默认空实现；子类可覆写以释放自己申请的资源
        （打开的文件/连接、临时目录、外部进程句柄等）。

        实现要求：
        * **异常会被吞掉并记日志**（清理失败不得改变任务终态）；
        * 不要在这里等待主线程或发信号——它运行在工作线程内，
          且此时 ``run()`` 尚未返回。
        """
        return None


def run_worker(
    worker: BackgroundWorker,
    # 回调返回值被 Qt 信号机制忽略——放宽为 object，允许调用方复用消息框返回值
    on_succeeded: Callable[[Any], object] | None = None,
    on_failed: Callable[[str], object] | None = None,
) -> BackgroundWorker:
    """启动后台任务并接线信号；结束自动 deleteLater 防止泄漏。"""
    if on_succeeded is not None:
        worker.succeeded.connect(on_succeeded)
    if on_failed is not None:
        worker.failed.connect(on_failed)
    worker.finished.connect(worker.deleteLater)
    worker.start()
    return worker
