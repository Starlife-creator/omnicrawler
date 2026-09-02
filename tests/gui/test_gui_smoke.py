from __future__ import annotations

import importlib.util
import sys
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="GUI smoke test requires PyQt6",
)


@pytest.fixture(autouse=True)
def _use_chinese_ui_language():
    """Keep GUI text assertions independent from earlier language-switch tests."""

    from omnicrawler.gui import i18n

    i18n.set_language("zh_CN")
    yield
    i18n.set_language("zh_CN")


def test_task_workspace_template_library_and_rebuild_start_offscreen(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.main import MainWindow, NavIndex, TemplateLibraryDialog
    from omnicrawler.gui.views.task_canvas import TaskCanvas

    monkeypatch.setattr(MainWindow, "_on_first_launch", lambda self: None)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    # P0：旧向导已替换为持续可编辑的任务工作台。
    assert isinstance(window._task_canvas, TaskCanvas)
    for name in ("_intent_section", "_draft_section", "_fields_section",
                 "_trial_section", "_delivery_section"):
        assert hasattr(window._task_canvas, name)
    assert "任务工作台" in window._nav.item(NavIndex.WORKSPACE).text()
    assert window._task_canvas._intent_section._title_label.text() == "任务目标"
    assert window._task_canvas._draft_section._title_label.text() == "采集方案"
    assert window._task_canvas._fields_section._title_label.text() == "字段规则"
    assert window._task_canvas._trial_section._title_label.text() == "试跑验证"
    assert window._task_canvas._delivery_section._title_label.text() == "输出与交付"
    assert window._task_canvas.persistent_action_bar().parent() is window._workspace_widget
    assert not window._task_canvas.isAncestorOf(window._task_canvas.persistent_action_bar())

    # 首页入口必须路由到语义一致的工作区，最近任务进入含任务历史的监控页。
    window._home.open_workspace.emit()
    assert window._nav.currentRow() == NavIndex.WORKSPACE
    window._home.open_recent.emit()
    assert window._nav.currentRow() == NavIndex.MONITOR

    # 简单模式保留完整核心闭环，专业/开发者工具按能力渐进披露。
    window._apply_ui_mode("simple")
    for index in (NavIndex.HOME, NavIndex.WORKSPACE, NavIndex.MONITOR, NavIndex.RESULTS):
        assert not window._nav.item(index).isHidden()
    for index in (NavIndex.YAML_EDITOR, NavIndex.EVIDENCE, NavIndex.PLUGIN_MARKET, NavIndex.DEVELOPER):
        assert window._nav.item(index).isHidden()
    window._apply_ui_mode("professional")
    assert not window._nav.item(NavIndex.YAML_EDITOR).isHidden()
    assert not window._nav.item(NavIndex.RESULTS).isHidden()
    assert window._nav.item(NavIndex.DEVELOPER).isHidden()
    window._apply_ui_mode("developer")
    assert all(not window._nav.item(index).isHidden() for index in range(window._nav.count()))

    templates = window._template_loader.discover_templates(force=True)
    assert len(templates) >= 50
    dialog = TemplateLibraryDialog(templates)
    dialog._search.setText("wordpress")
    assert dialog._list.count() >= 1

    window._refresh_canvas()
    assert isinstance(window._task_canvas, TaskCanvas)
    dialog.deleteLater()
    window.deleteLater()
    app.processEvents()


def test_toast_automatically_removes_itself_after_its_duration(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QMainWindow

    from omnicrawler.gui.widgets.toast import ToastOverlay

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
    from PySide6.QtCore import QThread
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.main import MainWindow

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


def test_unsaved_window_close_preserves_recovery_draft(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.main import MainWindow

    monkeypatch.setattr(MainWindow, "_on_first_launch", lambda self: None)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    calls: list[str] = []
    window._config_path = None
    monkeypatch.setattr(window._autosave, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(
        window._autosave, "delete_draft", lambda: calls.append("delete")
    )

    window.close()
    app.processEvents()

    assert calls == ["stop"]


def test_export_thread_reports_progress_periodically_not_for_every_row(tmp_path, monkeypatch):
    from omnicrawler.gui.views.result_table import ExportThread

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


def test_rebuild_project_components_keeps_new_components_alive(monkeypatch):
    """FINAL-G1 回归：重建项目组件必须销毁旧实例、保留新实例。

    原实现先重建后对 self._xxx（已指向新对象）deleteLater——新 TaskHistory
    在事件循环处理后即被销毁，后续访问抛 "Internal C++ object already
    deleted"，旧组件则整体泄漏。
    """
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.main import MainWindow

    monkeypatch.setattr(MainWindow, "_on_first_launch", lambda self: None)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    old_history = window._task_history
    old_autosave = window._autosave

    window._rebuild_project_components()
    # deleteLater 在事件循环中执行；处理后旧对象被销毁、新对象必须存活
    app.processEvents()

    assert window._task_history is not old_history
    assert window._autosave is not old_autosave
    # 新组件 C++ 对象必须仍然可用
    window._task_history.load_history()
    window.deleteLater()
    app.processEvents()


def test_export_thread_xlsx_escapes_formula_injection(tmp_path, monkeypatch):
    """B10 result_table：XLSX 单元格值必须以 excel_safe 转义，防 CWE-1236。"""
    from omnicrawler.gui.views.result_table import ExportThread

    source = tmp_path / "records.csv"
    source.write_text("name\n=SUM(A1:A2)\nplain\n", encoding="utf-8")

    captured: list[list] = []
    workbooks: list = []

    class FakeSheet:
        title = ""

        def __init__(self) -> None:
            self.cells: list[list] = []

        def cell(self, row, column, value=None):
            while len(self.cells) < row:
                self.cells.append([])
            while len(self.cells[row - 1]) < column:
                self.cells[row - 1].append(None)
            if value is not None:
                self.cells[row - 1][column - 1] = value
            return self.cells[row - 1][column - 1]

    class FakeWorkbook:
        def __init__(self) -> None:
            self.active = FakeSheet()
            self.sheetnames = ["Sheet1"]
            workbooks.append(self)

        def create_sheet(self, _name):
            sheet = FakeSheet()
            sheet.title = _name
            self.sheetnames.append(_name)
            return sheet

        @staticmethod
        def save(_path: str) -> None:
            return None

    monkeypatch.setitem(sys.modules, "openpyxl", SimpleNamespace(Workbook=FakeWorkbook))
    worker = ExportThread(source, tmp_path / "export.xlsx")
    worker.finished_signal.connect(lambda ok, message: captured.append((ok, message)))
    worker.run()

    assert captured == [(True, str(tmp_path / "export.xlsx"))]
    assert workbooks, "ExportThread 应创建 Workbook"
    values = [cell for row in workbooks[0].active.cells for cell in row]
    assert "'=SUM(A1:A2)" in values
    assert "plain" in values
