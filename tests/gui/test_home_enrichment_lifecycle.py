"""Regression coverage for repeated home tasks and exception details."""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from omnicrawler.gui.delegates.error_dialog import ErrorDialogHelper
from omnicrawler.gui.home import HomePage, _AIEnrichWorker


@pytest.fixture
def app():
    return QApplication.instance() or QApplication([])


def settle(app, condition):
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    assert condition()


def test_finished_worker_can_be_replaced_after_qt_deletion(app, monkeypatch):
    monkeypatch.setattr(_AIEnrichWorker, "run", lambda self: self.ai_unavailable.emit(self._request))
    home = HomePage()
    try:
        for request in ("first", "second", "third"):
            home._try_ai_enrich(SimpleNamespace(request=request))
            settle(app, lambda: home._enrich_worker is None)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert "third" in home.feedback.text()
    finally:
        home.shutdown()
        home.deleteLater()


def test_only_latest_request_runs_and_old_feedback_is_ignored(app, monkeypatch):
    release = threading.Event()
    started = threading.Event()
    requests = []

    def run(worker):
        requests.append(worker._request)
        if worker._request == "old":
            started.set()
            release.wait(5)
        worker.ai_unavailable.emit(worker._request)

    monkeypatch.setattr(_AIEnrichWorker, "run", run)
    home = HomePage()
    try:
        home._try_ai_enrich(SimpleNamespace(request="old"))
        settle(app, started.is_set)
        home._try_ai_enrich(SimpleNamespace(request="superseded"))
        home._try_ai_enrich(SimpleNamespace(request="latest"))
        home.feedback.setText("local draft")
        release.set()
        settle(app, lambda: home._enrich_worker is None)
        assert requests == ["old", "latest"]
        assert "latest" in home.feedback.text()
        assert "old" not in home.feedback.text()
    finally:
        release.set()
        home.shutdown()
        settle(app, lambda: home._enrich_worker is None)
        home.deleteLater()


def test_shutdown_cancels_pending_work_without_destroying_running_thread(app, monkeypatch):
    release = threading.Event()
    started = threading.Event()
    requests = []

    def run(worker):
        requests.append(worker._request)
        started.set()
        release.wait(5)
        worker.ai_error.emit("late error")

    monkeypatch.setattr(_AIEnrichWorker, "run", run)
    home = HomePage()
    try:
        home._try_ai_enrich(SimpleNamespace(request="old"))
        settle(app, started.is_set)
        worker = home._enrich_worker
        home._try_ai_enrich(SimpleNamespace(request="pending"))
        home.shutdown()
        home.shutdown()
        assert worker.isRunning()
        assert worker.isInterruptionRequested()
        release.set()
        settle(app, lambda: home._enrich_worker is None)
        assert requests == ["old"]
        assert "late error" not in home.feedback.text()
    finally:
        release.set()
        home.shutdown()
        settle(app, lambda: home._enrich_worker is None)
        home.deleteLater()


def test_exception_details_outside_except_preserve_traceback_and_redact(app, monkeypatch):
    def fail():
        raise ValueError("https://private.example/item?token=secret123")
    try:
        fail()
    except ValueError as caught:
        error = caught
    captured = {}

    def show(dialog):
        captured["details"] = dialog.detailedText()
        captured["summary"] = dialog.text()
        for button in dialog.buttons():
            if button.text() == "复制错误详情":
                button.click()
        captured["clipboard"] = app.clipboard().text()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", show)
    owner = HomePage()
    try:
        ErrorDialogHelper(owner).show_error_dialog(error, "Creating task")
        assert "Creating task" in captured["details"]
        assert "in fail" in captured["details"]
        assert "ValueError" in captured["details"]
        assert "NoneType: None" not in captured["details"]
        assert captured["details"] in captured["clipboard"]
        for value in captured.values():
            assert "private.example" not in value
            assert "secret123" not in value
    finally:
        owner.deleteLater()


def test_home_version_matches_running_application(app, monkeypatch):
    import importlib.metadata

    from PySide6.QtWidgets import QLabel

    from omnicrawler._version import __version__
    from omnicrawler.gui.home import AmbientHero, _package_version

    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.12.0")
    assert _package_version() == __version__
    hero = AmbientHero()
    assert __version__ in hero.findChild(QLabel, "eyebrow").text()
    hero.deleteLater()


def test_home_small_window_keeps_actions_separate_and_scrollable(app):
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QFrame

    home = HomePage()
    home.resize(780, 540)
    home.show()
    try:
        app.processEvents()
        assert home._scroll.verticalScrollBar().maximum() > 0
        card = home.findChild(QFrame, "quickTaskCard")
        input_bottom = home.task_input.mapTo(card, QPoint(0, home.task_input.height())).y()
        create_top = home.create_button.mapTo(card, QPoint(0, 0)).y()
        assert input_bottom < create_top
        home._scroll.ensureWidgetVisible(home.create_button)
        app.processEvents()
        viewport = home._scroll.viewport()
        point = home.create_button.mapTo(viewport, home.create_button.rect().center())
        assert viewport.rect().contains(point)
    finally:
        home.close()
        home.deleteLater()
