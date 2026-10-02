from __future__ import annotations

import time
from threading import Event

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from omnicrawler.gui.core.config_model import CrawlConfig
from omnicrawler.gui.core.workers import PageAnalyzeWorker
from omnicrawler.gui.views.task_canvas import TaskCanvas


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(.01)
    assert predicate()


@pytest.mark.parametrize("outcome", ["success", "failure", "cancel", "stale"])
def test_analysis_thread_is_owned_and_all_terminal_paths_restore_controls(app, monkeypatch, outcome):
    entered, release = Event(), Event()
    report = {"item_selector": "div.item", "fields": [{"name": "标题", "selector": "h2"}],
              "page_type": "list", "rendered": True}

    def work(worker):
        entered.set()
        assert release.wait(5)
        if outcome == "failure":
            raise RuntimeError("fixture analysis failure")
        return report, worker.url

    monkeypatch.setattr(PageAnalyzeWorker, "work", work)
    canvas = TaskCanvas(CrawlConfig(seed_urls=["https://example.org/js/"]))
    canvas.load_config(canvas._config)
    applied_threads = []
    original = canvas.apply_analysis

    def apply(result):
        applied_threads.append(QThread.currentThread())
        original(result)

    monkeypatch.setattr(canvas, "apply_analysis", apply)
    canvas._analyze_btn.click()
    worker = canvas._analyze_worker
    try:
        assert worker is not None and worker.parent() is canvas
        assert entered.wait(2)
        canvas._rebuild_from_config()
        assert not canvas._analyze_btn.isEnabled()
        canvas._analyze_page()
        assert canvas._analyze_worker is worker
        if outcome == "cancel":
            worker.requestInterruption()
        elif outcome == "stale":
            canvas._url_edit.setText("https://example.org/other")
        release.set()
        pump(app, lambda: canvas._analyze_worker is None)
        assert canvas._analyze_btn.isEnabled()
        if outcome == "success":
            assert applied_threads == [app.thread()]
            assert canvas._config.source_kind == "browser"
            assert canvas._item_selector_edit.text() == "div.item"
        else:
            assert applied_threads == []
            assert canvas._item_selector_edit.text() == ""
    finally:
        release.set()
        if canvas._analyze_worker is not None:
            canvas._analyze_worker.requestInterruption()
            canvas._analyze_worker.wait(5000)
            pump(app, lambda: canvas._analyze_worker is None)
        canvas.close()
