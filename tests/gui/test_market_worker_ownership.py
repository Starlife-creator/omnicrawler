"""Market windows retain live workers through close and deferred deletion."""
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThread
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid


def _view(kind, tmp_path):
    if kind == "plugin":
        from omnicrawler.gui.views.plugin_market import PluginMarketView
        return PluginMarketView(project_root=tmp_path)
    from omnicrawler.gui.views.template_market import TemplateMarketView
    return TemplateMarketView(project_root=tmp_path, catalog_url="", trust_source="")


def _worker(view):
    started = threading.Event()
    release = threading.Event()

    class Worker(QThread):
        def run(self):
            started.set()
            assert release.wait(5)

    worker = Worker(view)
    worker.start()
    assert started.wait(2)
    return worker, release


@pytest.mark.parametrize("kind", ["plugin", "template"])
def test_close_waits_for_owned_worker_and_ignores_late_catalog(kind, tmp_path):
    app = QApplication.instance() or QApplication([])
    view = _view(kind, tmp_path)
    worker, release = _worker(view)
    try:
        assert not view.close(), "A market must retain its running worker"
        assert worker.isRunning() and worker.isInterruptionRequested()
        before = view._catalog
        view._on_catalog_loaded({"plugins": [], "templates": [], "_source": "late"})
        assert view._catalog == before
    finally:
        release.set()
        assert worker.wait(5000)
        app.processEvents()
        if isValid(view):
            view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("kind", ["plugin", "template"])
def test_deferred_delete_retains_worker_until_native_finished(kind, tmp_path):
    app = QApplication.instance() or QApplication([])
    view = _view(kind, tmp_path)
    worker, release = _worker(view)
    try:
        view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert isValid(view) and worker.isRunning()
        assert worker.isInterruptionRequested()
        release.set()
        assert worker.wait(5000)
        deadline = time.monotonic() + 5
        while isValid(view) and time.monotonic() < deadline:
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(view)
    finally:
        release.set()
        if isValid(worker):
            assert worker.wait(5000)
        if isValid(view):
            view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
