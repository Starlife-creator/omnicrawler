"""Real scan threads must be owned, update the GUI thread, and stop before close."""
from __future__ import annotations

import time
from pathlib import Path
from threading import Event

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

from omnicrawler.gui.views.pdf_workbench import PdfWorkbenchView


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def pump(app, predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert predicate()


def test_scan_is_owned_and_results_are_applied_on_gui_thread(app, tmp_path, monkeypatch):
    pdf = tmp_path / "file.PDF"
    pdf.write_bytes(b"sample")
    view = PdfWorkbenchView()
    threads = []
    apply = view._apply_scan_result

    def capture(result):
        threads.append(QThread.currentThread())
        apply(result)

    monkeypatch.setattr(view, "_apply_scan_result", capture)
    view._dir_input.setText(str(tmp_path))
    view._scan_btn.click()
    worker = view._scan_worker
    assert worker is not None and worker.parent() is view
    assert worker.wait(5000)
    pump(app, lambda: view._scan_worker is None)
    assert threads == [app.thread()]
    assert view._pdf_files == [pdf]
    assert view._state == "ready" and view._execute_btn.isEnabled()
    # A second real scan is safe after the first worker has been released.
    view._scan_btn.click()
    pump(app, lambda: view._scan_worker is None)
    assert view._pdf_files == [pdf]
    view.close()


@pytest.mark.parametrize("action", ["cancel", "close"])
def test_scan_cancel_and_close_wait_for_thread_without_blocking_gui(app, tmp_path, monkeypatch, action):
    (tmp_path / "file.pdf").write_bytes(b"sample")
    entered, release = Event(), Event()
    original = Path.rglob

    def blocked(root, pattern):
        entered.set()
        assert release.wait(5)
        yield from original(root, pattern)

    monkeypatch.setattr(Path, "rglob", blocked)
    view = PdfWorkbenchView()
    view.show()
    view._dir_input.setText(str(tmp_path))
    view._scan_btn.click()
    worker = view._scan_worker
    try:
        assert worker is not None and entered.wait(5)
        view._scan_directory()
        assert view._scan_worker is worker
        started = time.monotonic()
        if action == "cancel":
            view._cancel_btn.click()
        else:
            view.close()
            assert view.isVisible()  # Destruction is deferred until the worker stops.
        assert time.monotonic() - started < 1
        assert worker.isInterruptionRequested()
    finally:
        release.set()
        assert worker is not None and worker.wait(5000)
    pump(app, lambda: view._scan_worker is None)
    assert view._pdf_files == []
    assert view._state == "idle"
    if action == "close":
        pump(app, lambda: not view.isVisible())
        view.show()
        assert not view._close_requested
        view._scan_btn.click()
        pump(app, lambda: view._scan_worker is None)
        assert view._state == "ready"
        view.close()
    else:
        assert view._scan_btn.isEnabled() and view._directory_group.isEnabled()
        assert not view._execute_btn.isEnabled()
        view.close()


def test_empty_and_failed_scan_restore_controls(app, tmp_path, monkeypatch):
    view = PdfWorkbenchView()
    view._dir_input.setText(str(tmp_path))
    view._scan_btn.click()
    pump(app, lambda: view._scan_worker is None)
    assert view._state == "idle" and not view._execute_btn.isEnabled()

    def failed(*args):
        raise OSError("scan denied")

    monkeypatch.setattr(Path, "rglob", failed)
    view._scan_btn.click()
    pump(app, lambda: view._scan_worker is None)
    assert "scan denied" in view._scan_status.text()
    assert view._scan_btn.isEnabled() and view._directory_group.isEnabled()
    view.close()
