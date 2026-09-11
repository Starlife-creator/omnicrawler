"""`audit-20260805` 逐条复核的修复证据（40 条里的「仍存在」部分）。

对应条目见 `优化方案.md` §十 的复核表。这些修复的共同点是：**原缺陷都不报错**
（静默无响应、静默忽略用户输入、静默写错地方），所以必须由断言而不是肉眼来守。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("PySide6", reason="GUI 修复验证需要 PySide6")
from PySide6.QtWidgets import QApplication, QMainWindow

from omnicrawler.gui import i18n
from omnicrawler.gui.main import MainWindow, NavIndex


@pytest.fixture(scope="module")
def qt_only() -> QApplication:
    """只要一个 QApplication（不需要整窗口）的用例用这个。"""
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    return app


@pytest.fixture(autouse=True)
def _chinese_ui() -> None:
    i18n.set_language("zh_CN")


@pytest.fixture
def window(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[MainWindow]:
    """构造一个真实的 MainWindow（跳过首启引导，避免弹窗）。"""
    monkeypatch.setattr(MainWindow, "_on_first_launch", lambda self: None)
    app = QApplication.instance() or QApplication([])
    assert app is not None
    win = MainWindow()
    yield win
    win.close()


# ---------------------------------------------------------------------------
# §A-11：防重入标志真正生效 + 去抖定时器不再堆积
# ---------------------------------------------------------------------------


def test_workspace_sync_uses_single_debounce_timer(window: MainWindow) -> None:
    timer = window._workspace_sync_timer
    assert timer.isSingleShot(), "去抖必须用单次定时器"
    assert timer.interval() == 300


def test_workspace_sync_flag_is_true_during_sync_only(window: MainWindow) -> None:
    """★ 原实现把标志在同一次调用里置真又立刻置假，等于没有防重入。"""
    observed: list[bool] = []
    original = window._yaml_editor.update_from_config

    def spy(config: object) -> None:
        observed.append(window._updating_editor)
        original(config)

    window._yaml_editor.update_from_config = spy  # type: ignore[method-assign]
    try:
        window._on_workspace_changed()
        assert window._updating_editor is False, "同步开始前不应为真（否则会挡住正常变更）"
        window._sync_workspace_to_editor()
        assert observed == [True], "同步期间标志必须为真（否则挡不住回声）"
        assert window._updating_editor is False, "同步结束后必须复位"
    finally:
        window._yaml_editor.update_from_config = original  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# §A-24：工作台/编辑器切换必须同步侧栏
# ---------------------------------------------------------------------------


def test_toggle_workspace_editor_keeps_sidebar_in_sync(window: MainWindow) -> None:
    window._nav.setCurrentRow(NavIndex.WORKSPACE)
    assert window._stack.currentIndex() == window._nav_pages[NavIndex.WORKSPACE]

    window._toggle_workspace_editor()
    assert window._nav.currentRow() == NavIndex.YAML_EDITOR, "侧栏选中态没有跟着切换"
    assert window._stack.currentIndex() == window._nav_pages[NavIndex.YAML_EDITOR]

    window._toggle_workspace_editor()
    assert window._nav.currentRow() == NavIndex.WORKSPACE


# ---------------------------------------------------------------------------
# §A-25：历史结果缺 records.csv 时不再静默无响应
# ---------------------------------------------------------------------------


def test_history_results_without_csv_reports_to_user(
    window: MainWindow, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from omnicrawler.gui.widgets.toast import ToastManager

    warned: list[str] = []
    monkeypatch.setattr(ToastManager, "warning", lambda self, message, **_kw: warned.append(message))

    window._load_history_results(str(tmp_path / "empty-workspace"))

    assert warned, "缺 records.csv 时没有任何用户可见提示（仍是静默无响应）"


def test_history_results_with_csv_loads(window: MainWindow, tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    (workspace / "output").mkdir(parents=True)
    (workspace / "output" / "records.csv").write_text("a,b\n1,2\n", encoding="utf-8")

    window._load_history_results(str(workspace))

    assert window._nav.currentRow() == NavIndex.RESULTS
    assert window._result_table.current_path is not None


# ---------------------------------------------------------------------------
# §A-34：公开路径接口（主窗口不再读私有 `_filepath`）
# ---------------------------------------------------------------------------


def test_views_expose_public_current_path(window: MainWindow) -> None:
    assert window._result_table.current_path is None
    assert window._chart_view.current_path is None


# ---------------------------------------------------------------------------
# §A-27：首页 PDF 指引不再是死胡同
# ---------------------------------------------------------------------------


def test_pdf_request_is_consumed_by_pdf_workbench(window: MainWindow) -> None:
    """★ 原实现只写 `setProperty("last_nl_request", …)`，全仓无人读取。"""
    window._home.setProperty("last_nl_request", "把这几份 PDF 里的表格抽出来")
    window._nav.setCurrentRow(NavIndex.PDF_WORKBENCH)

    assert window._pdf_workbench._pending_request == "把这几份 PDF 里的表格抽出来"


def test_pdf_button_navigates_and_passes_request(window: MainWindow) -> None:
    window._home.setProperty("last_nl_request", "处理这批 PDF")
    window._home.open_pdf_workbench.emit("处理这批 PDF")
    assert window._nav.currentRow() == NavIndex.PDF_WORKBENCH
    assert window._pdf_workbench._pending_request == "处理这批 PDF"


# ---------------------------------------------------------------------------
# §A-8：复制示例复制的是「当前显示的条目」
# ---------------------------------------------------------------------------


def test_copy_example_uses_displayed_entry(qt_only: QApplication) -> None:
    from omnicrawler.gui.help_center import HelpCenterDock

    dock = HelpCenterDock()
    dock.show_help("task.intent", reveal=False)
    expected = dock._current_entry.example if dock._current_entry else ""
    dock._copy_example()
    clipboard = qt_only.clipboard()
    assert clipboard is not None
    assert clipboard.text() == expected


def test_copy_example_without_example_warns_instead_of_silence(
    qt_only: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnicrawler.gui.help_center import HelpCenterDock
    from omnicrawler.gui.widgets.toast import ToastManager

    dock = HelpCenterDock()
    dock.show_help("不存在的帮助 id", reveal=False)  # 走兜底条目，example 为空
    told: list[str] = []
    monkeypatch.setattr(ToastManager, "info", lambda self, message, **_kw: told.append(message))

    dock._copy_example()

    assert told, "空示例时静默复制空串——用户会以为按钮坏了"


# ---------------------------------------------------------------------------
# §A-22：QSettings 已销毁时的读/sync 防护
# ---------------------------------------------------------------------------


class _DestroyedQSettings:
    """模拟「Qt 侧对象已被销毁」的 QSettings（测试/插件重载/退出到启动器后重建应用）。

    整体替换 `AppSettings._settings` 而不是 monkeypatch `QSettings.value`——后者是
    C 扩展类型，改不了它的属性。
    """

    def value(self, *args: object, **kwargs: object) -> object:
        raise RuntimeError("Internal C++ object already deleted")

    def setValue(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("Internal C++ object already deleted")

    def sync(self) -> None:
        raise RuntimeError("Internal C++ object already deleted")

    def isWritable(self) -> bool:
        raise RuntimeError("Internal C++ object already deleted")

    def status(self) -> object:
        raise RuntimeError("Internal C++ object already deleted")


def test_settings_read_survives_destroyed_qsettings(monkeypatch: pytest.MonkeyPatch) -> None:
    from omnicrawler.gui.settings import AppSettings

    settings = AppSettings.instance()
    monkeypatch.setattr(settings, "_settings", _DestroyedQSettings())
    settings._session_values.clear()
    assert settings._value("theme", "light", str) == "light"
    assert settings._value("recent/files", [], list) == []


def test_settings_write_and_sync_survive_destroyed_qsettings(monkeypatch: pytest.MonkeyPatch) -> None:
    from omnicrawler.gui.settings import AppSettings

    settings = AppSettings.instance()
    monkeypatch.setattr(settings, "_settings", _DestroyedQSettings())
    settings._set_value("theme", "dark")  # 会话回退仍然生效
    assert settings._session_values["theme"] == "dark"
    settings.sync()  # 不应抛出


# ---------------------------------------------------------------------------
# §A-43：快捷键改绑必须持久化
# ---------------------------------------------------------------------------


def test_rebind_persists_to_settings(window: MainWindow) -> None:
    from omnicrawler.gui.settings import AppSettings
    from omnicrawler.gui.shortcuts import GlobalShortcutManager

    manager = GlobalShortcutManager(window)
    manager.register_all({"save": lambda: None})
    assert manager.rebind("save", "Ctrl+Alt+S") is True

    assert AppSettings.instance().shortcuts["save"] == "Ctrl+Alt+S"


# ---------------------------------------------------------------------------
# §A-41：死代码已删除
# ---------------------------------------------------------------------------


def test_icon_registry_has_no_dead_color_map() -> None:
    from omnicrawler.gui.icon_registry import IconRegistry

    assert not hasattr(IconRegistry, "_color_map"), "未被读取的死属性又被加回来了"


# ---------------------------------------------------------------------------
# §A-17：首次运行日期真正进入调度
# ---------------------------------------------------------------------------


def test_schedule_start_date_reaches_store(qt_only: QApplication, tmp_path: Path) -> None:
    """★ 此前该输入框只显示日期，从未进入调度（调度器按 next_run_at 判到期）。"""
    from omnicrawler.gui.views.project_dialogs import ScheduleManagerDialog
    from omnicrawler.runtime.scheduler import ScheduleStore

    config = tmp_path / "task.yaml"
    config.write_text("project: {name: t, workspace: /tmp/w}\n", encoding="utf-8")
    database = tmp_path / "schedules.sqlite3"

    # 父窗口必须留一个 Python 引用：临时对象会被 GC，连带销毁 dialog 的 C++ 对象
    host = QMainWindow()
    dialog = ScheduleManagerDialog(host, database=database, resolve_current_config=lambda: config)
    dialog._start_date_label.setText("2026-12-31")
    dialog._add_current()

    with ScheduleStore(database) as store:
        rows = store.list()
    assert len(rows) == 1, rows
    # 2026-12-31 本地时间戳，明显晚于「立即开始」（time.time()）
    assert rows[0]["next_run_at"] > 1_700_000_000


def test_schedule_without_start_date_starts_now(qt_only: QApplication, tmp_path: Path) -> None:
    from omnicrawler.gui.views.project_dialogs import ScheduleManagerDialog
    from omnicrawler.runtime.scheduler import ScheduleStore

    config = tmp_path / "task.yaml"
    config.write_text("project: {name: t, workspace: /tmp/w}\n", encoding="utf-8")
    database = tmp_path / "schedules.sqlite3"

    # 父窗口必须留一个 Python 引用：临时对象会被 GC，连带销毁 dialog 的 C++ 对象
    host = QMainWindow()
    dialog = ScheduleManagerDialog(host, database=database, resolve_current_config=lambda: config)
    assert dialog._start_date_label.text() == ""
    dialog._add_current()

    with ScheduleStore(database) as store:
        rows = store.list()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# §A-36：模板收藏走 AppSettings 这一唯一真源
# ---------------------------------------------------------------------------


def test_template_favorites_go_through_app_settings(qt_only: QApplication) -> None:
    from omnicrawler.gui.settings import AppSettings
    from omnicrawler.gui.template_library_dialog import TemplateLibraryDialog

    dialog = TemplateLibraryDialog([])
    assert dialog._settings is AppSettings.instance(), "又开了一个独立 QSettings"

    dialog._favorites = {"demo"}
    dialog._settings.favorite_templates = sorted(dialog._favorites)
    assert AppSettings.instance().favorite_templates == ["demo"]
