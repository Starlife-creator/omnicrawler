import threading
import time
from types import SimpleNamespace

from PySide6.QtWidgets import QApplication, QMessageBox

from omnicrawler.gui.views.change_monitor import NewRuleDialog


def test_single_check_cannot_replace_active_worker():
    from omnicrawler.gui.views.change_monitor import ChangeMonitorView

    worker = object()
    view = SimpleNamespace(_shutting_down=False, _worker=worker, _rules_data=[{"rule_id": "r1"}])
    ChangeMonitorView._check_single(view, "r1")
    assert view._worker is worker


def test_deferred_delete_waits_for_owned_probe(monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid

    from omnicrawler.fetching import page_probe

    app = QApplication.instance() or QApplication([])
    started, release = threading.Event(), threading.Event()

    def safe_fetch(*args, **kwargs):
        started.set()
        release.wait(3)
        return SimpleNamespace(body=b"hello", headers={}, status=200)

    monkeypatch.setattr(page_probe, "fetch_static_page", safe_fetch)
    dialog = NewRuleDialog()
    dialog._url_edit.setText("https://example.com")
    try:
        dialog._probe_url()
        assert started.wait(1)
        worker = dialog._probe_worker
        dialog.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert isValid(dialog), "Active owned thread must finish before destruction"
        assert worker.isInterruptionRequested()
        release.set()
        assert worker.wait(3000)
        deadline = time.monotonic() + 2
        while isValid(dialog) and time.monotonic() < deadline:
            app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not isValid(dialog)
    finally:
        release.set()
        if isValid(dialog):
            if dialog._probe_worker:
                dialog._probe_worker.wait(3000)
            app.processEvents()
            dialog.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_probe_runs_off_gui_thread_and_defers_close_until_finished(monkeypatch):
    import urllib.request

    from omnicrawler.fetching import page_probe

    app = QApplication.instance() or QApplication([])
    main_thread = threading.get_ident()
    started, release = threading.Event(), threading.Event()
    calls = []
    messages = []

    def safe_fetch(*args, **kwargs):
        calls.append(threading.get_ident())
        started.set()
        release.wait(2)
        return SimpleNamespace(body=b"hello", headers={"content-type": "text/html"}, status=200)

    def direct_fetch(*args, **kwargs):
        calls.append(threading.get_ident())
        raise AssertionError("direct transport")

    monkeypatch.setattr(page_probe, "fetch_static_page", safe_fetch, raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", direct_fetch)
    monkeypatch.setattr(QMessageBox, "information", lambda *a: messages.append(a))
    monkeypatch.setattr(QMessageBox, "warning", lambda *a: messages.append(a))
    dialog = NewRuleDialog()
    dialog._url_edit.setText("https://example.com")
    try:
        dialog._probe_url()
        assert started.wait(1), "探测未通过安全后台抓取启动"
        assert calls and all(thread != main_thread for thread in calls)
        worker = dialog._probe_worker
        dialog.reject()
        assert worker.isRunning()
        release.set()
        assert worker.wait(3000)
        deadline = time.monotonic() + 1
        while dialog._probe_worker is not None and time.monotonic() < deadline:
            app.processEvents()
        assert dialog._probe_worker is None
        assert not messages, "取消后不得显示晚到成功或错误消息"
    finally:
        release.set()
        worker = getattr(dialog, "_probe_worker", None)
        if worker is not None:
            worker.wait(3000)
        app.processEvents()
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
