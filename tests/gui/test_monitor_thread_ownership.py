import threading

import pytest
import shiboken6
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication

from omnicrawler.gui.views.change_monitor import ChangeMonitorView, _CheckWorker


def test_closing_standalone_monitor_stops_future_polling():
    app = QApplication.instance() or QApplication([])
    view = ChangeMonitorView()
    view.close()
    assert not view._timer.isActive()
    assert view._shutting_down
    view.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.mark.parametrize("operation", ["result", "close", "delete"])
def test_result_does_not_release_live_thread_and_deletion_waits(operation):
    app = QApplication.instance() or QApplication([])
    view = ChangeMonitorView()
    entered, release = threading.Event(), threading.Event()

    class PausedCheck(_CheckWorker):
        def run(self):
            self.checked.emit([])
            entered.set()
            assert release.wait(5)

    worker = PausedCheck([], view)
    completed = QSignalSpy(worker.finished)
    view._worker = worker
    view._start_check(worker, view._on_check_finished)
    try:
        assert entered.wait(5)
        QTest.qWait(25)
        assert completed.count() == 0
        assert view._worker is worker and worker.isRunning()
        if operation == "close":
            view.show()
            assert not view.close()
            assert view.isVisible() and worker.is_cancelled()
        elif operation == "delete":
            view.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert shiboken6.isValid(view) and worker.is_cancelled()
    finally:
        release.set()
        assert worker.wait(5000)
        app.processEvents()
        assert completed.count() == 1
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        if shiboken6.isValid(view):
            assert view._worker is None
            view.close()
            view.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    if operation == "delete":
        assert not shiboken6.isValid(view)
