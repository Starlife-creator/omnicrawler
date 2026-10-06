"""Keep widget-owned threads alive until their native finished signal."""
from PySide6.QtCore import QCoreApplication, QEvent, QThread
from PySide6.QtWidgets import QWidget

from ..i18n import _


class WorkerOwnedWidget(QWidget):
    """Defer final close and deletion without blocking the GUI thread."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName(_("任务视图"))
        self._worker_shutdown_requested = False
        self._close_after_workers = False
        self._delete_after_workers = False
        self._watched_workers: set[QThread] = set()

    def shutdown(self) -> None:
        self._worker_shutdown_requested = True
        self.setEnabled(False)
        for worker in self.findChildren(QThread):
            if not worker.isRunning():
                continue
            if worker not in self._watched_workers:
                self._watched_workers.add(worker)
                worker.finished.connect(self._finish_owned_workers)
            worker.requestInterruption()
            worker.quit()

    def _workers_running(self) -> bool:
        return any(worker.isRunning() for worker in self.findChildren(QThread))

    def _finish_owned_workers(self) -> None:
        if self._workers_running():
            return
        if self._close_after_workers:
            self._close_after_workers = False
            self.close()
        if self._delete_after_workers:
            self._delete_after_workers = False
            QCoreApplication.postEvent(self, QEvent(QEvent.Type.DeferredDelete))

    def event(self, event: QEvent) -> bool:
        if event.type() in {QEvent.Type.Close, QEvent.Type.DeferredDelete}:
            self.shutdown()
            if self._workers_running():
                if event.type() == QEvent.Type.Close:
                    self._close_after_workers = True
                    event.ignore()
                else:
                    self._delete_after_workers = True
                return True
        return super().event(event)
