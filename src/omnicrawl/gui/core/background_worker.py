"""BackgroundWorker 基类（S3.1.1）。

统一的后台任务基类：QThread + 结果/失败信号 + 取消 + 自动清理。
GUI 阻塞点（环境检测、pip 安装、导出、扫描、渲染、预检）统一接入，
保证点击后界面保持可交互。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QThread, pyqtSignal


class BackgroundWorker(QThread):
    """后台工作线程基类。

    子类实现 :meth:`work`（在工作线程执行），结果经 :attr:`succeeded`
    回传主线程；异常经 :attr:`failed` 回传。任务取消（requestInterruption）
    后不发射成功信号。
    """

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

    def run(self) -> None:
        try:
            result = self.work()
        except Exception as exc:  # noqa: BLE001 - 跨线程边界统一收口
            self.failed.emit(str(exc))
            return
        if not self.isInterruptionRequested():
            self.succeeded.emit(result)

    def work(self) -> Any:
        """在工作线程中执行的阻塞任务；子类必须实现。"""
        raise NotImplementedError

    def cleanup(self) -> None:
        """任务结束后的资源清理（工作线程内调用）。"""
        return None


def run_worker(
    worker: BackgroundWorker,
    on_succeeded: Callable[[Any], None] | None = None,
    on_failed: Callable[[str], None] | None = None,
) -> BackgroundWorker:
    """启动后台任务并接线信号；结束自动 deleteLater 防止泄漏。"""
    if on_succeeded is not None:
        worker.succeeded.connect(on_succeeded)
    if on_failed is not None:
        worker.failed.connect(on_failed)
    worker.finished.connect(worker.deleteLater)
    worker.start()
    return worker
