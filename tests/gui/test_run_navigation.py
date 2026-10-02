from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QListWidget,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QWidget,
)

from omnicrawler.gui.core.config_model import CrawlConfig, FieldDef
from omnicrawler.gui.delegates.run_controller import RunController
from omnicrawler.gui.navigation import NavIndex

_APP = None


@pytest.mark.parametrize("started", [True, False])
def test_run_selects_monitor_and_workspace_returns_directly(tmp_path, monkeypatch, started):
    global _APP
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    _APP = QApplication.instance() or QApplication([])
    window = QWidget()
    window._nav = QListWidget(window)
    window._nav.addItems(["工作", "首页", "工作台", "运行与历史"])
    window._stack = QStackedWidget(window)
    for _ in range(3):
        window._stack.addWidget(QWidget())
    pages = {NavIndex.WORKSPACE: 0, NavIndex.HOME: 1, NavIndex.MONITOR: 2}
    window._nav.currentRowChanged.connect(lambda row: window._stack.setCurrentIndex(pages.get(row, 0)))
    window._config = CrawlConfig(seed_urls=["https://example.org/"], fields=[FieldDef("标题", "h2")])
    window._config_path = tmp_path / "task.yaml"
    window._project_root = tmp_path
    window._omnicrawler_available = True
    window._task_runner = SimpleNamespace(is_running=False, start=lambda cfg: started,
                                         config_path=window._config_path, get_pid=lambda: None)
    window._task_history = SimpleNamespace(add_record=lambda **kwargs: None)
    window._resource_monitor = SimpleNamespace(set_pid=lambda pid: None)
    window._log_console = SimpleNamespace(clear=lambda: None)
    window._set_status = lambda message: None
    window._progress_bar = QProgressBar(window)
    for name in ("_run_btn", "_stop_btn", "_pause_btn"):
        setattr(window, name, QPushButton(window))
    for name in ("_progress_url_label", "_elapsed_label"):
        setattr(window, name, QLabel(window))
    window._run_delegate = RunController(window)
    monkeypatch.setattr(window._run_delegate, "_ensure_dependencies", lambda: True)
    try:
        window._nav.setCurrentRow(NavIndex.WORKSPACE)
        window._run_delegate.run_task()
        assert window._nav.currentRow() == NavIndex.MONITOR
        assert window._stack.currentIndex() == pages[NavIndex.MONITOR]
        window._nav.setCurrentRow(NavIndex.WORKSPACE)
        assert window._stack.currentIndex() == pages[NavIndex.WORKSPACE]
        window._nav.setCurrentRow(NavIndex.HOME)
        assert window._stack.currentIndex() == pages[NavIndex.HOME]
    finally:
        timer = getattr(window, "_task_elapsed_timer", None)
        if timer is not None:
            timer.stop()
        window.close()
        window.deleteLater()
        _APP.processEvents()
