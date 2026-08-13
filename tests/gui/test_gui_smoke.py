from __future__ import annotations

import importlib.util
import sys
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PyQt6") is None,
    reason="GUI smoke test requires PyQt6",
)


def test_five_step_gui_template_library_and_rebuild_start_offscreen(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    from omnicrawl.gui.main import MainWindow, TemplateLibraryDialog
    from omnicrawl.gui.views.task_canvas import TaskCanvas

    monkeypatch.setattr(MainWindow, "_on_first_launch", lambda self: None)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    # P0：五步向导已替换为任务画布（Task Canvas）
    assert isinstance(window._task_canvas, TaskCanvas)
    for name in ("_intent_section", "_draft_section", "_fields_section",
                 "_trial_section", "_delivery_section"):
        assert hasattr(window._task_canvas, name)

    templates = window._template_loader.discover_templates(force=True)
    assert len(templates) >= 50
    dialog = TemplateLibraryDialog(templates)
    dialog._search.setText("wordpress")
    assert dialog._list.count() >= 1

    window._rebuild_wizard()
    assert isinstance(window._task_canvas, TaskCanvas)
    dialog.deleteLater()
    window.deleteLater()
    app.processEvents()


def test_toast_automatically_removes_itself_after_its_duration(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication, QMainWindow

    from omnicrawl.gui.widgets.toast import ToastOverlay

    app = QApplication.instance() or QApplication([])
    window = QMainWindow()
    window.resize(800, 600)
    window.show()
    overlay = ToastOverlay(window)
    toast = overlay.show_toast("自动消失验证", duration=60)

    assert toast._timer.isActive()
    # The full suite may have queued Qt cleanup from an earlier GUI test.  Poll
    # the event loop instead of assuming a fixed 400ms wall-clock budget.
    for _ in range(80):
        if not overlay._toasts:
            break
        QTest.qWait(25)
        app.processEvents()

    assert overlay._toasts == [], (
        f"timer_active={toast._timer.isActive()} closing={toast._closing} "
        f"close_animation={toast._close_animation is not None}"
    )
    assert overlay.isHidden()
    window.deleteLater()
    app.processEvents()


def test_window_defers_close_until_auxiliary_thread_stops(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtCore import QThread
    from PyQt6.QtTest import QTest
    from PyQt6.QtWidgets import QApplication

    from omnicrawl.gui.main import MainWindow

    monkeypatch.setattr(MainWindow, "_on_first_launch", lambda self: None)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    thread = QThread(window)
    thread.start()
    QTest.qWait(25)

    window.close()

    assert window._close_after_background_jobs is True
    QTest.qWait(300)
    app.processEvents()
    assert not thread.isRunning()
    assert not window.isVisible()


def test_export_thread_reports_progress_periodically_not_for_every_row(tmp_path, monkeypatch):
    from omnicrawl.gui.views.result_table import ExportThread

    source = tmp_path / "records.csv"
    source.write_text("name\n" + "item\n" * 10_000, encoding="utf-8")

    class FakeSheet:
        title = ""

        @staticmethod
        def cell(*_args, **_kwargs):
            return None

    class FakeWorkbook:
        def __init__(self) -> None:
            self.active = FakeSheet()
            self.sheetnames = ["Sheet1"]

        @staticmethod
        def save(_path: str) -> None:
            return None

    monkeypatch.setitem(sys.modules, "openpyxl", SimpleNamespace(Workbook=FakeWorkbook))
    progress: list[int] = []
    completed: list[tuple[bool, str]] = []
    worker = ExportThread(source, tmp_path / "export.xlsx")
    worker.progress.connect(progress.append)
    worker.finished_signal.connect(lambda ok, message: completed.append((ok, message)))

    worker.run()

    assert progress == [10]
    assert completed == [(True, str(tmp_path / "export.xlsx"))]
