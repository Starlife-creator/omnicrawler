"""OmniCrawler GUI 工作台 — 主入口。

启动 Application 主窗口，支持 --headless/--run 无 GUI 模式参数。

架构说明：MainWindow 是组合根，通过 8 个 delegate 类分发功能：
  MenuBuilder / ToolbarManager / ThemeManager / ErrorDialogHelper
  EnvironmentChecker / HelpDialogManager / RunDelegate / ConfigDelegate
每个 delegate 用 ``__getattr__`` 透明转发到 MainWindow 属性。
"""

from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .. import __version__ as APP_VERSION  # noqa: N812
from ..core.logging_utils import configure_logging
from ..core.runtime_paths import (
    application_dir,
    configure_runtime_environment,
    is_frozen,
    package_resource,
    portable_data_root,
    resolve_cli_command,
)
from .i18n import _
from .navigation import NavIndex  # noqa: F401  # S3.1.2 re-export


def _cli_mode() -> bool:
    """S3.1.7：只读判定 CLI 模式（无副作用）。"""
    return any(arg in ("--headless", "--run") for arg in sys.argv[1:])


_GUI_APP_HOLD = None

if not _cli_mode():
    try:
        from PyQt6.QtCore import (
            QObject,
            QSettings,
            Qt,
            QThread,
            QTimer,
            QUrl,
            pyqtSignal,
            pyqtSlot,
        )
        from PyQt6.QtGui import (
            QAction,
            QDesktopServices,
        )
        from PyQt6.QtWidgets import (
            QApplication,
            QCheckBox,
            QComboBox,
            QDialog,
            QDialogButtonBox,
            QFormLayout,
            QHBoxLayout,
            QInputDialog,
            QLabel,
            QLineEdit,
            QListWidget,
            QListWidgetItem,
            QMainWindow,
            QMenu,
            QMessageBox,
            QProgressBar,
            QPushButton,
            QSpinBox,
            QSplitter,
            QStackedWidget,
            QStatusBar,
            QSystemTrayIcon,
            QVBoxLayout,
            QWidget,
            QWizard,
        )
    except ImportError as e:
        print(_(f"PyQt6 未安装，无法启动图形界面: {e}"), file=sys.stderr)
        print(_("请运行: pip install omnicrawl-platform[gui]"), file=sys.stderr)
        sys.exit(1)

    from ..core.ai_env import (
        load_ai_config_sidecar,
        load_ai_env,
        save_ai_config_sidecar,
        save_ai_env,
        sync_ai_env_to_os,
    )
    from ..core.config import load_config as load_core_config
    from ..core.credentials import seal_secret
    from ..pipeline_ops.preflight import run_preflight, run_sample
    from ..plugins.plugin_inspector import inspect_directory
    from ..review.run_compare import compare_runs
    from ..services.application_service import ApplicationService
    from ..services.config_history import ConfigHistory
    from ..services.controllers import ResultController, RunController, TaskController
    from ..services.natural_language_task import NaturalLanguageDraft
    from ..services.offline_demo import create_demo_workspace
    from ..services.ux_service import QuickTaskDraft
    from ..sources.site_inspector import inspect_url
    from ..state import StateStore
    from ..templates.recipe_engine import compose_recipe, diff_config
    from ..templates.template_catalog import bundled_template_catalog
    from .async_workers import AsyncWorkerManager
    from .core.autosave import AutosaveManager
    from .core.config_model import CrawlConfig
    from .core.config_serializer import from_yaml, load_yaml, to_yaml
    from .core.template_loader import TemplateInfo, TemplateLoader
    from .delegates import (
        ConfigManager as ConfigDelegate,
    )
    from .delegates import (
        EnvironmentChecker,
        ErrorDialogHelper,
        HelpDialogManager,
        MenuBuilder,
        ThemeManager,
        ToolbarManager,
    )
    from .delegates import (
        RunController as RunDelegate,
    )
    from .design_system import PageTransitionController, VisualTokens
    from .help_center import HelpCenterDock
    from .home import HomePage
    from .runner.env_checker import (
        find_project_root,
    )
    from .runner.worker_task_runner import WorkerTaskRunner as TaskRunner
    from .settings import AppSettings
    from .shortcuts import GlobalShortcutManager
    from .views.change_monitor import ChangeMonitorView
    from .views.chart_view import ChartView
    from .views.file_list import FileList
    from .views.pdf_region_selector import PdfRegionSelectorDialog
    from .views.pdf_workbench import PdfWorkbenchView
    from .views.plugin_market import PluginMarketView
    from .views.professional_review import EvidenceView  # ProfessionalReviewView 别名保持兼容
    from .views.result_table import ResultTable
    from .views.task_history import TaskHistory
    from .views.yaml_editor import YamlEditor
    from .widgets.log_console import LogConsole
    from .widgets.resource_monitor import ResourceMonitor
    from .widgets.status_indicator import StatusIndicator
    from .widgets.toast import ToastManager
    from .wizard.step1_source import Step1SourcePage
    from .wizard.step2_urls import Step2UrlsPage
    from .wizard.step3_fields import Step3FieldsPage
    from .wizard.step4_download import Step4DownloadPage
    from .wizard.step5_preview import Step5PreviewPage

    GUI_VERSION = APP_VERSION


def _thread_interrupted() -> bool:
    """Qt may return ``None`` before a QObject has entered its worker thread."""
    thread = QThread.currentThread()
    return thread is not None and thread.isInterruptionRequested()


class ConfigWizard(QWizard):
    """五步配置向导。"""

    finish_requested = pyqtSignal()

    def __init__(self, config: CrawlConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config

        self.setWindowTitle(_("OmniCrawler 配置向导"))
        self.setMinimumSize(0, 0)
        self.setWizardStyle(QWizard.WizardStyle.ModernStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, False)

        self._step1 = Step1SourcePage(config)
        self._step2 = Step2UrlsPage(config)
        self._step3 = Step3FieldsPage(config)
        self._step4 = Step4DownloadPage(config)
        self._step5 = Step5PreviewPage(config)

        self.addPage(self._step1)
        self.addPage(self._step2)
        self.addPage(self._step3)
        self.addPage(self._step4)
        self.addPage(self._step5)

        self.setButtonText(QWizard.WizardButton.BackButton, _("← 上一步"))
        self.setButtonText(QWizard.WizardButton.NextButton, _("下一步 →"))
        self.setButtonText(QWizard.WizardButton.FinishButton, _("完成并保存"))
        self.setButtonText(QWizard.WizardButton.CancelButton, _("取消"))
        for button_id in (
            QWizard.WizardButton.BackButton,
            QWizard.WizardButton.NextButton,
            QWizard.WizardButton.FinishButton,
            QWizard.WizardButton.CancelButton,
        ):
            button = self.button(button_id)
            if button is not None:
                button.setMinimumHeight(36)
        next_button = self.button(QWizard.WizardButton.NextButton)
        if next_button is not None:
            assert isinstance(next_button, QPushButton)
            next_button.setDefault(True)
            next_button.setProperty("primary", True)

        for page in [self._step1, self._step2, self._step3, self._step4, self._step5]:
            page.config_changed.connect(self._on_config_changed)  # type: ignore[attr-defined]

    def accept(self) -> None:
        self.finish_requested.emit()

    def set_simple_mode(self, enabled: bool) -> None:
        for page in (self._step2, self._step3, self._step4):
            if hasattr(page, "set_simple_mode"):
                page.set_simple_mode(enabled)

    @property
    def step5_page(self) -> Step5PreviewPage:
        return self._step5

    @property
    def step2_page(self) -> Step2UrlsPage:
        return self._step2

    @property
    def step1_page(self) -> Step1SourcePage:
        return self._step1

    def _on_config_changed(self) -> None:
        pass


class SiteInspectionWorker(QObject):
    finished = pyqtSignal(object, str)
    failed = pyqtSignal(str)

    def __init__(self, url: str, intent: str = "") -> None:
        super().__init__()
        self.url = url
        self.intent = intent

    @pyqtSlot()
    def run(self) -> None:
        try:
            if _thread_interrupted():
                return
            report = inspect_url(
                self.url, bundled_template_catalog(), intent=self.intent
            ).to_dict()
        except Exception as exc:
            if not _thread_interrupted():
                self.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            if not _thread_interrupted():
                self.finished.emit(report, self.url)


class TemplateLibraryDialog(QDialog):
    """Searchable category view that remains usable with hundreds of templates."""

    def __init__(self, templates: list[TemplateInfo], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("模板库"))
        self.resize(720, 520)
        self._templates = templates
        self.selected_template: TemplateInfo | None = None
        self._settings = QSettings("OmniCrawler", "GUIWorkbench")
        stored_favorites = self._settings.value("templates/favorites", [])
        if isinstance(stored_favorites, str):
            stored_favorites = [stored_favorites]
        self._favorites = {str(value) for value in stored_favorites or []}

        layout = QVBoxLayout(self)
        filters = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText(_("搜索名称、说明或标签…"))
        self._category = QComboBox()
        self._category.addItem(_("全部分类"), "")
        for category in sorted({item.category for item in templates}):
            self._category.addItem(category, category)
        self._favorite_only = QCheckBox(_("只看收藏"))
        filters.addWidget(self._search, 1)
        filters.addWidget(self._category)
        filters.addWidget(self._favorite_only)
        layout.addLayout(filters)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        layout.addWidget(self._list, 1)
        self._description = QLabel()
        self._description.setWordWrap(True)
        self._description.setMinimumHeight(55)
        layout.addWidget(self._description)
        self._favorite_button = QPushButton(_("☆ 收藏/取消收藏"))
        self._favorite_button.clicked.connect(self._toggle_favorite)
        layout.addWidget(self._favorite_button)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
        )
        _open_btn = buttons.button(QDialogButtonBox.StandardButton.Open)
        assert _open_btn is not None
        _open_btn.setText(_("加载模板"))
        buttons.accepted.connect(self._accept_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._search.textChanged.connect(self._refresh)
        self._category.currentIndexChanged.connect(self._refresh)
        self._favorite_only.toggled.connect(self._refresh)
        self._list.currentItemChanged.connect(self._show_description)
        self._list.itemDoubleClicked.connect(lambda _item: self._accept_selected())
        self._refresh()

    def _refresh(self) -> None:
        query = self._search.text().strip().casefold()
        category = str(self._category.currentData() or "")
        self._list.clear()
        templates = sorted(
            self._templates,
            key=lambda item: (item.template_id not in self._favorites, item.category, item.display_name),
        )
        for template in templates:
            haystack = " ".join(
                (template.name, template.description, template.category, *template.tags)
            ).casefold()
            if query and query not in haystack:
                continue
            if category and template.category != category:
                continue
            if self._favorite_only.isChecked() and template.template_id not in self._favorites:
                continue
            item = QListWidgetItem(
                f"{template.display_name}  ·  {template.category}  ·  v{template.version}"
                + (_(f"  ·  验证 {template.verified_at}") if template.verified_at else _("  ·  未标注验证日期"))
            )
            item.setText(("★ " if template.template_id in self._favorites else "☆ ") + item.text())
            item.setData(Qt.ItemDataRole.UserRole, template.template_id)
            item.setToolTip(template.description)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)
        else:
            self._description.setText(_("没有匹配的模板"))

    def _show_description(self, item: QListWidgetItem | None, _previous=None) -> None:
        template = self._find(item)
        if template:
            capabilities = "、".join(template.capabilities) or _("未声明")
            source = _("内置模板") if template.is_builtin else _("用户模板")
            self._description.setText(
                f"{template.description}\n"
                + _(f"适用：{template.recommended_when or '请结合目标网址试跑判断'}\n")
                + _(f"为什么推荐：{template.why or '由网址、页面结构、数据源和所需能力综合判断'}\n")
                + _(f"限制：{template.limitations or '未声明特殊限制'}\n")
                + _(f"能力：{capabilities}\n来源：{source}；文件：{template.filepath}")
            )
        else:
            self._description.setText("")

    def _find(self, item: QListWidgetItem | None) -> TemplateInfo | None:
        template_id = item.data(Qt.ItemDataRole.UserRole) if item else None
        return next((value for value in self._templates if value.template_id == template_id), None)

    def _accept_selected(self) -> None:
        self.selected_template = self._find(self._list.currentItem())
        if self.selected_template is not None:
            self.accept()

    def _toggle_favorite(self) -> None:
        template = self._find(self._list.currentItem())
        if template is None:
            return
        if template.template_id in self._favorites:
            self._favorites.remove(template.template_id)
        else:
            self._favorites.add(template.template_id)
        self._settings.setValue("templates/favorites", sorted(self._favorites))
        self._refresh()


class ActionRecorderWorker(QObject):
    finished = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, url: str, output: Path) -> None:
        super().__init__()
        self._url = url
        self._output = output

    @pyqtSlot()
    def run(self) -> None:
        try:
            from ..fetching.action_recorder import record_with_playwright
            if _thread_interrupted():
                return
            result = record_with_playwright(self._url, self._output)
            if not _thread_interrupted():
                self.finished.emit(result)
        except Exception as exc:
            if not _thread_interrupted():
                self.failed.emit(str(exc))


class SampleRunWorker(QObject):
    finished = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, config_path: Path, pages: int = 3) -> None:
        super().__init__()
        self._config_path = config_path
        self._pages = pages

    @pyqtSlot()
    def run(self) -> None:
        try:
            if _thread_interrupted():
                return
            result = run_sample(load_core_config(self._config_path), pages=self._pages)
            if not _thread_interrupted():
                self.finished.emit(result)
        except Exception as exc:
            if not _thread_interrupted():
                self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    """OmniCrawler GUI 主窗口 — 组合根，通过 delegate 分发功能。"""

    # ---- 类级类型声明（由 delegate.setup() 在 __init__ 中创建） ----
    _recent_menu: QMenu
    _schedule_action: QAction
    _run_btn: QPushButton
    _stop_btn: QPushButton
    _pause_btn: QPushButton
    _toggle_btn: QPushButton
    _resource_label: QLabel
    _resource_profile_combo: QComboBox
    _mode_combo: QComboBox
    _visual_tokens: VisualTokens
    _omnicrawl_available: bool
    _omnicrawl_path: str
    _project_root: Path
    _dnd_mode: bool
    _statusbar: QStatusBar
    _tray_icon: QSystemTrayIcon | None
    # 委托/后台构建的方法内赋值属性（方法内局部 import 类型，注解延迟求值）
    _log_console: Any
    _page_transition: Any
    _running_task_id: str | None
    _preflight_pending: bool

    def __init__(self) -> None:
        global _GUI_APP_HOLD
        _GUI_APP_HOLD = QApplication.instance()
        super().__init__()
        self.setObjectName("omnicrawlerMainWindow")
        self.setWindowTitle(_("OmniCrawler GUI 工作台 v{0}").format(GUI_VERSION))
        self.setMinimumSize(860, 520)
        self.resize(1180, 760)

        # ---- 核心状态 ----
        self._settings = AppSettings.instance()
        self._config = CrawlConfig()
        self._config_path: Path | None = None
        self._task_controller: TaskController | None = None
        self._run_controller: RunController | None = None
        self._result_controller: ResultController | None = None
        self._updating_editor = False
        self._updating_wizard = False
        self._dnd_mode = self._settings.dnd_enabled

        # ---- 创建 delegates ----
        self._menu_builder = MenuBuilder(self)
        self._toolbar_manager = ToolbarManager(self)
        self._theme_manager = ThemeManager(self)
        self._error_helper = ErrorDialogHelper(self)
        self._env_checker = EnvironmentChecker(self)
        self._help_dialogs = HelpDialogManager(self)
        self._run_delegate = RunDelegate(self)
        self._config_delegate = ConfigDelegate(self)

        # ---- 项目根目录 ----
        project_root_str = self._settings.project_root
        if project_root_str and Path(project_root_str).is_dir():
            self._project_root = Path(project_root_str)
        else:
            # S4.2 ⑤：兜底不再用 cwd（随启动位置漂移）——冻结用数据根，
            # 源码环境用应用目录（稳定，不指向随意的启动目录）
            found = portable_data_root() if is_frozen() else find_project_root()
            fallback = portable_data_root() if is_frozen() else application_dir()
            self._project_root = found or fallback
            self._settings.project_root = str(self._project_root)
        self._config_history = ConfigHistory(self._project_root / ".config_history")

        # ---- 环境检测 ----
        self._omnicrawl_path = resolve_cli_command(self._settings.omnicrawl_path)
        if self._omnicrawl_path != self._settings.omnicrawl_path:
            self._settings.omnicrawl_path = self._omnicrawl_path
        self._omnicrawl_available = False

        # ---- 核心组件（S3.1.27：抽方法，切换项目可重建）----
        self._build_project_components()

        # ---- 运行状态 ----
        self._task_start_time: datetime | None = None
        self._task_elapsed_timer: QTimer | None = None
        self._inspection_jobs: list[tuple[QThread, SiteInspectionWorker]] = []
        self._sample_jobs: list[tuple[QThread, SampleRunWorker]] = []
        self._recorder_thread: QThread | None = None
        self._recorder_worker: ActionRecorderWorker | None = None
        self._close_after_background_jobs = False
        self._async_manager = AsyncWorkerManager()

        # ---- 构建 UI ----
        self._refresh_accessibility()
        self._ensure_data_mode_choice()
        # F53：数据模式弹窗可能重置设置单例（settings.ini 换目录），重新绑定当前实例
        self._settings = AppSettings.instance()
        self._setup_menu_bar()
        self._setup_toolbar()
        self._setup_status_bar()
        self._setup_central_area()
        self._setup_help_center()
        self._apply_ui_mode(self._settings.ui_mode)
        self._setup_system_tray()

        # ---- 连接信号 ----
        self._connect_signals()
        self._setup_global_shortcuts()
        ToastManager.instance().bind(self)
        QTimer.singleShot(100, self._on_first_launch)

    # ================================================================
    #  Delegate 转发方法
    # ================================================================

    # --- MenuBuilder ---
    def _setup_menu_bar(self) -> None:
        self._menu_builder.setup()

    # --- ToolbarManager ---
    def _setup_toolbar(self) -> None:
        self._toolbar_manager.setup()

    # --- ThemeManager ---
    def _apply_ui_mode(self, mode: str) -> None:
        self._theme_manager.apply_ui_mode(mode)

    def _change_resource_profile(self) -> None:
        self._theme_manager.change_resource_profile()

    def _refresh_accessibility(self) -> None:
        self._theme_manager.refresh_accessibility()

    def _apply_visual_theme(self) -> None:
        self._theme_manager.apply_visual_theme()

    def _set_interface_scale(self, value: int) -> None:
        self._theme_manager.set_interface_scale(value)

    def _set_accessibility_option(self, name: str, value: bool) -> None:
        self._theme_manager.set_accessibility_option(name, value)

    def _set_theme(self, theme: str) -> None:
        self._theme_manager.set_theme(theme)

    def _toggle_dnd(self, enabled: bool) -> None:
        self._theme_manager.toggle_dnd(enabled)

    def _update_dnd_label(self) -> None:
        self._theme_manager.update_dnd_label()

    # --- ErrorDialogHelper ---
    def _show_error_dialog(self, exc: Exception, context: str = "", *, retry_callback=None) -> None:
        self._error_helper.show_error_dialog(exc, context, retry_callback=retry_callback)

    def _redact_error(self, text: str) -> str:
        return self._error_helper.redact_error(text)

    # --- EnvironmentChecker ---
    def _ensure_data_mode_choice(self) -> None:
        self._env_checker.ensure_data_mode_choice()

    def _check_environment(self, silent: bool = True) -> bool:
        return self._env_checker.check_environment(silent)

    def _recheck_env(self) -> None:
        self._env_checker.recheck_env()

    def _switch_project(self) -> None:
        self._env_checker.switch_project()

    def _update_project_label(self) -> None:
        self._env_checker.update_project_label()

    def _on_first_launch(self) -> None:
        self._env_checker.on_first_launch()

    def _show_welcome_dialog(self) -> None:
        self._env_checker.show_welcome_dialog()

    def _show_env_setup_dialog(self) -> None:
        self._env_checker.show_env_setup_dialog()

    def _quick_experience(self) -> None:
        self._env_checker.quick_experience()

    # --- HelpDialogManager ---
    def _show_selector_help(self) -> None:
        self._help_dialogs.show_selector_help()

    def _show_quick_start(self) -> None:
        self._help_dialogs.show_quick_start()

    def _show_faq(self) -> None:
        self._help_dialogs.show_faq()

    def _show_shortcuts(self) -> None:
        self._help_dialogs.show_shortcuts()

    def _show_about(self) -> None:
        self._help_dialogs.show_about()

    def _show_capabilities(self) -> None:
        self._help_dialogs.show_capabilities()

    # --- RunDelegate ---
    def _toggle_pause(self) -> None:
        self._run_delegate.toggle_pause()

    def _run_task(self) -> None:
        self._run_delegate.run_task()

    def _stop_task(self) -> None:
        self._run_delegate.stop_task()

    def _update_elapsed(self) -> None:
        self._run_delegate.update_elapsed()

    @pyqtSlot(str, str)
    def _on_log_line(self, message: str, level: str) -> None:
        self._run_delegate.on_log_line(message, level)

    @pyqtSlot(int, str)
    def _on_progress(self, percent: int, url: str) -> None:
        self._run_delegate.on_progress(percent, url)

    @pyqtSlot(str)
    def _on_task_state_changed(self, state: str) -> None:
        self._run_delegate.on_task_state_changed(state)

    @pyqtSlot(str, int)
    def _on_task_finished(self, task_id: str, exit_code: int) -> None:
        self._run_delegate.on_task_finished(task_id, exit_code)

    # --- ConfigDelegate ---
    def _new_config(self) -> None:
        self._config_delegate.new_config()

    def _open_config(self) -> None:
        self._config_delegate.open_config()

    def _open_recent(self, filepath: str) -> None:
        self._config_delegate._open_recent(filepath)

    def _save_config(self) -> None:
        self._config_delegate.save_config()

    def _save_config_as(self) -> None:
        self._config_delegate.save_config_as()

    def _refresh_recent_menu(self) -> None:
        self._config_delegate.refresh_recent_menu()

    def _clear_recent(self) -> None:
        self._config_delegate.clear_recent()

    def _export_config_package(self) -> None:
        self._config_delegate.export_config_package()

    def _import_config_package(self) -> None:
        self._config_delegate.import_config_package()

    def _import_config_package_from_path(self, path: Path) -> None:
        self._config_delegate._import_from_path(path)

    def _show_config_history(self) -> None:
        self._config_delegate.show_config_history()

    # ================================================================
    #  UI 构建
    # ================================================================

    def _setup_status_bar(self) -> None:
        sb = self.statusBar()
        assert sb is not None
        self._statusbar = sb
        self._project_label = QLabel()
        self._project_label.setObjectName("muted")
        self._update_project_label()
        self._statusbar.addWidget(self._project_label)
        self._config_label = QLabel(_("未保存"))
        self._config_label.setObjectName("muted")
        self._statusbar.addWidget(self._config_label)
        self._status_indicator = StatusIndicator(size=12)
        self._statusbar.addPermanentWidget(self._status_indicator)
        self._status_text = QLabel(_("空闲"))
        self._statusbar.addPermanentWidget(self._status_text)
        self._dnd_label = QLabel()
        self._dnd_label.setVisible(self._dnd_mode)
        self._statusbar.addPermanentWidget(self._dnd_label)
        self._update_dnd_label()
        self._finish_label = QLabel()
        self._finish_label.setObjectName("muted")
        self._statusbar.addPermanentWidget(self._finish_label)

    def _setup_help_center(self) -> None:
        self._help_center = HelpCenterDock(self)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._help_center)
        self._help_center.hide()

    def _setup_central_area(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._nav = QListWidget()
        self._nav.setObjectName("mainNavigation")
        self._nav.setAccessibleName(_("主导航"))
        self._nav.setFixedWidth(190)

        nav_items = [
            ("⌂ " + _("首页"), 4),
            ("⚙ " + _("配置向导"), 0),
            ("📄 " + _("PDF 工作台"), 6),
            ("📝 " + _("YAML 编辑器"), 1),
            ("📋 " + _("任务监控"), 2),
            ("📊 " + _("结果与复核"), 3),
            ("🔍 " + _("证据查看器"), 5),
            ("🔔 " + _("变更监控"), 7),
            ("🧩 " + _("插件市场"), 8),
        ]
        for label, _idx in nav_items:
            item = QListWidgetItem(label)
            self._nav.addItem(item)

        self._nav.currentRowChanged.connect(self._on_nav_changed)
        main_layout.addWidget(self._nav)

        self._stack = QStackedWidget()

        self._wizard_widget = QWidget()
        wizard_layout = QVBoxLayout(self._wizard_widget)
        wizard_layout.setContentsMargins(0, 0, 0, 0)
        self._advanced_summary = QLabel("")
        self._advanced_summary.setWordWrap(True)
        self._advanced_summary.setAccessibleName(_("已启用高级规则摘要"))
        self._advanced_summary.setObjectName("advancedSummary")
        self._advanced_summary.setProperty("status", "warning")
        wizard_layout.addWidget(self._advanced_summary)

        # 拆分器：左侧配置向导 + 右侧信息面板
        wizard_splitter = QSplitter(Qt.Orientation.Horizontal)
        # S3.1.2：保存 splitter 引用——重建向导时操作 splitter 而非外层 layout
        self._wizard_splitter = wizard_splitter
        self._config_wizard = ConfigWizard(self._config)
        wizard_splitter.addWidget(self._config_wizard)

        # 右侧信息面板（200px 宽，只读）
        self._wizard_info_panel = QLabel("")
        self._wizard_info_panel.setObjectName("wizardInfoPanel")
        self._wizard_info_panel.setWordWrap(True)
        self._wizard_info_panel.setMinimumWidth(200)
        self._wizard_info_panel.setMaximumWidth(240)
        self._wizard_info_panel.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._wizard_info_panel.setTextFormat(Qt.TextFormat.RichText)
        wizard_splitter.addWidget(self._wizard_info_panel)
        wizard_splitter.setStretchFactor(0, 3)
        wizard_splitter.setStretchFactor(1, 1)
        wizard_splitter.setSizes([600, 200])

        wizard_layout.addWidget(wizard_splitter)
        self._stack.addWidget(self._wizard_widget)

        # 监听向导页面切换以更新信息面板
        self._config_wizard.currentIdChanged.connect(self._update_wizard_info_panel)

        self._yaml_editor = YamlEditor()
        self._yaml_editor.sync_to_form.connect(self._on_editor_sync_to_form)
        self._yaml_editor.sync_status.connect(self._set_status)
        self._yaml_editor.config_changed.connect(self._on_editor_config_changed)
        self._stack.addWidget(self._yaml_editor)

        monitor_widget = QWidget()
        monitor_layout = QVBoxLayout(monitor_widget)
        monitor_layout.setContentsMargins(8, 8, 8, 8)

        status_layout = QHBoxLayout()
        self._monitor_status = StatusIndicator(size=16)
        status_layout.addWidget(self._monitor_status)
        self._monitor_status_text = QLabel(_("空闲"))
        status_layout.addWidget(self._monitor_status_text)
        status_layout.addStretch()
        self._elapsed_label = QLabel("")
        status_layout.addWidget(self._elapsed_label)
        monitor_layout.addLayout(status_layout)

        self._progress_bar = QProgressBar()
        self._progress_bar.setRange(0, 100)
        self._progress_bar.setValue(0)
        self._progress_bar.setTextVisible(True)
        monitor_layout.addWidget(self._progress_bar)

        self._progress_url_label = QLabel("")
        self._progress_url_label.setObjectName("muted")
        monitor_layout.addWidget(self._progress_url_label)

        monitor_layout.addWidget(self._resource_monitor)

        self._log_console = LogConsole()
        monitor_layout.addWidget(self._log_console)

        ctrl_layout = QHBoxLayout()
        ctrl_run = QPushButton(_("▶ 运行"))
        ctrl_run.clicked.connect(self._run_task)
        ctrl_layout.addWidget(ctrl_run)
        ctrl_stop = QPushButton(_("■ 停止"))
        ctrl_stop.clicked.connect(self._stop_task)
        ctrl_layout.addWidget(ctrl_stop)
        ctrl_pause = QPushButton(_("Ⅱ 暂停/继续"))
        ctrl_pause.clicked.connect(self._toggle_pause)
        ctrl_layout.addWidget(ctrl_pause)
        ctrl_layout.addStretch()
        monitor_layout.addLayout(ctrl_layout)

        self._task_history.load_history()
        monitor_layout.addWidget(self._task_history)
        self._stack.addWidget(monitor_widget)

        results_widget = QWidget()
        results_layout = QVBoxLayout(results_widget)
        results_layout.setContentsMargins(8, 8, 8, 8)
        self._result_table = ResultTable()
        self._result_table.record_selected_for_review.connect(self._on_record_selected_for_review)
        results_layout.addWidget(self._result_table)
        self._file_list = FileList()
        results_layout.addWidget(self._file_list)
        self._chart_view = ChartView()
        results_layout.addWidget(self._chart_view)
        self._stack.addWidget(results_widget)

        self._home = HomePage(project_root=str(self._project_root))
        self._home.quick_task_ready.connect(self._apply_quick_task)
        self._home.natural_task_ready.connect(self._apply_natural_task)
        self._home.open_wizard.connect(lambda: self._nav.setCurrentRow(NavIndex.WIZARD))
        self._home.open_recent.connect(lambda: self._nav.setCurrentRow(NavIndex.YAML_EDITOR))
        # S3.1.2：修复"结果与复核"错页（原误用 NavIndex.MONITOR）
        self._home.open_results.connect(lambda: self._nav.setCurrentRow(NavIndex.RESULTS))
        self._home.open_schedule.connect(self._manage_schedules)
        self._home.import_task.connect(self._import_config_package)
        self._home.run_doctor.connect(self._recheck_env)
        self._home.create_demo.connect(self._create_offline_demo)
        self._stack.addWidget(self._home)

        self._evidence_view = EvidenceView()
        self._evidence_view.back_to_results.connect(lambda: self._nav.setCurrentRow(NavIndex.RESULTS))  # 返回结果与复核页
        self._stack.addWidget(self._evidence_view)
        self._pdf_workbench = PdfWorkbenchView()
        self._stack.addWidget(self._pdf_workbench)

        self._change_monitor = ChangeMonitorView(settings=self._settings)
        self._change_monitor.desktop_notify.connect(self._on_monitor_desktop_notify)
        self._stack.addWidget(self._change_monitor)

        self._plugin_market = PluginMarketView(project_root=self._project_root)
        self._stack.addWidget(self._plugin_market)

        main_layout.addWidget(self._stack)
        self._page_transition = PageTransitionController(
            self._stack, reduced_motion=self._settings.reduced_motion,
        )
        self._nav.setCurrentRow(NavIndex.HOME)

    def _build_project_components(self) -> None:
        """S3.1.27：构建/重建依赖项目根的组件（switch_project 时复用）。"""
        self._config_history = ConfigHistory(self._project_root / ".config_history")
        self._task_runner = TaskRunner(
            omnicrawl_path=self._omnicrawl_path,
            project_root=self._project_root,
        )
        self._task_runner.log_line.connect(self._on_log_line)
        self._task_runner.progress.connect(self._on_progress)
        self._task_runner.state_changed.connect(self._on_task_state_changed)
        self._task_runner.task_finished.connect(self._on_task_finished)

        self._autosave = AutosaveManager(self._project_root)
        self._autosave.draft_found.connect(self._on_draft_found)

        self._template_loader = TemplateLoader(
            builtin_dir=package_resource("omnicrawl", "templates"),
            user_dir=self._project_root / "templates",
            additional_builtin_dirs=(package_resource("omnicrawl", "gui", "templates"),),
        )
        self._task_history = TaskHistory(
            self._project_root,
            max_entries=self._settings.history_max_entries,
            max_days=self._settings.history_max_days,
        )
        self._task_history.load_config_requested.connect(self._load_history_config)
        self._task_history.view_results_requested.connect(self._load_history_results)
        self._resource_monitor = ResourceMonitor(project_root=self._project_root)

    def _rebuild_project_components(self) -> None:
        """S3.1.27：切换项目后重建依赖项目根的组件（不再只改标签）。"""
        self._build_project_components()
        # 三者均为 QObject 子类（模板加载器 QObject、自动保存/历史 QWidget）
        for widget in (self._autosave, self._template_loader, self._task_history):
            if isinstance(widget, QWidget):
                widget.deleteLater()

    def _setup_system_tray(self) -> None:
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray_icon = QSystemTrayIcon(self)
            self._tray_icon.setToolTip(_("OmniCrawler GUI 工作台"))
            self._tray_icon.activated.connect(self._on_tray_activated)
            # S3.1.2：QMenu(self) 接管所有权，托盘右键菜单不因父对象销毁而悬空
            tray_menu = QMenu(self)
            show_action = QAction(_("显示主窗口"), self)
            show_action.triggered.connect(self.show)
            tray_menu.addAction(show_action)
            quit_action = QAction(_("退出"), self)
            quit_action.triggered.connect(self.close)
            tray_menu.addAction(quit_action)
            self._tray_icon.setContextMenu(tray_menu)
            self._tray_icon.show()
        else:
            self._tray_icon = None

    # ================================================================
    #  信号连接
    # ================================================================

    def _connect_signals(self) -> None:
        for page_id in self._config_wizard.pageIds():
            page = self._config_wizard.page(page_id)
            if page is not None and hasattr(page, 'config_changed'):
                page.config_changed.connect(self._on_wizard_changed)  # type: ignore[attr-defined]
            if page is not None and hasattr(page, 'inspect_requested'):
                page.inspect_requested.connect(self._inspect_site)  # type: ignore[attr-defined]
            if page is not None and hasattr(page, 'record_requested'):
                page.record_requested.connect(self._record_browser_actions)  # type: ignore[attr-defined]
        self._connect_wizard_actions()

    def _connect_wizard_actions(self) -> None:
        self._config_wizard.finish_requested.connect(self._save_config)
        step5 = self._config_wizard.step5_page
        step5.save_requested.connect(self._save_config)
        step5.save_as_requested.connect(self._save_config_as)
        step5.sample_requested.connect(self._show_preflight)
        step5.run_requested.connect(self._run_task)

    def _setup_global_shortcuts(self) -> None:
        self._shortcut_manager = GlobalShortcutManager(self)
        self._shortcut_manager.register_all({
            "save": self._save_config,
            "run": self._run_task,
            "stop": self._stop_task,
            "toggle_editor": self._toggle_wizard_editor,
            "open_templates": self._show_template_library,
            "refresh": self._refresh_results_page,
            "format_yaml": self._format_yaml_from_shortcut,
            "toggle_dnd": lambda: self._toggle_dnd(not self._dnd_mode),
        })

    # ================================================================
    #  导航与页面切换
    # ================================================================

    def _refresh_results_page(self) -> None:
        if self._result_controller is not None:
            self._result_controller.query()
        if hasattr(self, "_result_table") and self._result_table._filepath:
            self._result_table.refresh()
        if hasattr(self, "_chart_view") and self._chart_view._filepath:
            self._chart_view.load_csv(self._chart_view._filepath)
        ToastManager.instance().success(_("结果页已刷新"))

    def _format_yaml_from_shortcut(self) -> None:
        if self._stack.currentIndex() == 1:
            self._yaml_editor._format_yaml()
        else:
            ToastManager.instance().warning(_("请先切换到 YAML 编辑器"))

    def _on_nav_changed(self, index: int) -> None:
        # nav index -> stack page: 首页(4), 配置向导(0), PDF工作台(6), YAML编辑器(1), 任务监控(2), 结果复核(3), 证据查看器(5), 变更监控(7)
        page = (4, 0, 6, 1, 2, 3, 5, 7)[index] if 0 <= index < 8 else 4
        self._page_transition.show(page)
        if page == 1:
            self._yaml_editor.update_from_config(self._config)
        elif page == 3:
            self._auto_load_results()
        elif page == 5:
            pass  # 证据查看器数据由 record_selected_for_review 信号加载

    def _apply_quick_task(self, draft: QuickTaskDraft) -> None:
        self._apply_task_draft(draft)
        self._rebuild_wizard()
        self._nav.setCurrentRow(NavIndex.WIZARD)
        self._set_status(_("快速草案已生成：请查看自动决定和修改入口，然后先试跑"))

    def _apply_natural_task(self, draft: NaturalLanguageDraft) -> None:
        """Preserve the user's words and extracted topics when entering the wizard."""
        self._apply_task_draft(draft.task)
        self._config.task_description = draft.request
        if draft.topics:
            self._config.topic_include_any = list(dict.fromkeys(topic for topic in draft.topics if topic.strip()))
        self._rebuild_wizard()
        self._nav.setCurrentRow(NavIndex.WIZARD)
        cadence = {"weekly": _("每周"), "daily": _("每天"), "monthly": _("每月"), "manual": _("手动")}
        self._set_status(_("已从自然语言生成草案；建议频率：{0}。请确认第一页内容并先试跑。").format(
            cadence.get(draft.schedule, draft.schedule)
        ))

    def _on_record_selected_for_review(self, record: dict) -> None:
        """从结果表格跳转到证据查看器。"""
        self._evidence_view.show_record(record)
        self._nav.setCurrentRow(NavIndex.EVIDENCE)  # 导航到证据查看器

    def _apply_task_draft(self, draft: QuickTaskDraft) -> None:
        self._config.seed_urls = [draft.url]
        self._config.task_intent = draft.intent
        self._config.source_kind = draft.source_kind
        self._config.max_pages = draft.max_pages
        self._config.download.enabled = draft.download_files
        if draft.download_files and ".pdf" not in self._config.download.extensions:
            self._config.download.extensions.append(".pdf")
        self._config.process_pdf = draft.process_pdf
        self._config.monitor_same_url = draft.monitor_changes
        self._config.incremental = draft.monitor_changes
        self._config.output_formats = list(draft.output_formats)

    def _create_offline_demo(self) -> None:
        demo = create_demo_workspace(self._project_root / "demos" / "offline-onboarding")
        try:
            self._config = load_yaml(demo.config)
            self._config_path = demo.config
            self._config_label.setText(str(demo.config))
            self._rebuild_wizard()
            self._nav.setCurrentRow(NavIndex.WIZARD)
            self._set_status(_("离线演示已准备：无需网络，可直接查看并试跑"))
        except (OSError, ValueError) as exc:
            self._show_error_dialog(exc, _("创建离线演示"))

    def _toggle_wizard_editor(self) -> None:
        current = self._stack.currentIndex()
        if current == 1:
            self._stack.setCurrentIndex(0)
            self._toggle_btn.setText(_("⇄ 编辑器"))
        elif current == 0:
            self._stack.setCurrentIndex(1)
            self._yaml_editor.update_from_config(self._config)
            self._toggle_btn.setText(_("⇄ 向导"))

    def _bind_application_controllers(self) -> None:
        if self._config_path is None:
            self._task_controller = self._run_controller = self._result_controller = None
            return
        service = ApplicationService(self._config_path)
        self._task_controller = TaskController(service)
        self._run_controller = RunController(service)
        self._result_controller = ResultController(service)
        session_file = Path(service.load()["config"]["workspace"]) / "worker-session.json"
        if session_file.is_file() and not self._task_runner.is_running:
            self._task_runner.attach(session_file)

    # ================================================================
    #  向导/编辑器同步
    # ================================================================

    def _on_wizard_changed(self) -> None:
        if self._updating_editor:
            return
        self._updating_editor = True
        QTimer.singleShot(300, self._sync_wizard_to_editor)
        self._updating_editor = False

    def _update_wizard_info_panel(self, page_id: int) -> None:
        """更新配置向导右侧信息面板，显示当前步骤的字段帮助。"""
        tips: dict[int, str] = {
            0: (
                _("<h3>📌 Step 1：选择数据源</h3>") +

                _("<p><b>是什么</b>：设定要采集的网站或本地文件路径。</p>") +

                _("<p><b>为什么</b>：这是所有后续规则的根基——源类型决定了解析策略。</p>") +

                _("<p><b>示例</b>：<code>https://example.com/news</code> 或 <code>./data/pdfs/</code></p>") +

                _("<hr><p style='color:gray;font-size:11px'>💡 支持 HTTP/HTTPS 网页和本地 file:// 路径。</p>")
            ),
            1: (
                _("<h3>🔗 Step 2：发现 URL</h3>") +

                _("<p><b>是什么</b>：定义如何从首页发现更多目标页面。</p>") +

                _("<p><b>为什么</b>：控制爬取范围和深度，避免无限扩展。</p>") +

                _("<p><b>示例</b>：CSS 选择器 <code>a.article-link</code> 或正则 <code>/post/\\d+</code></p>") +

                _("<hr><p style='color:gray;font-size:11px'>💡 默认限制同域名，防止意外跳转到外部站点。</p>")
            ),
            2: (
                _("<h3>📋 Step 3：定义字段</h3>") +

                _("<p><b>是什么</b>：指定要从每个页面提取的数据字段。</p>") +

                _("<p><b>为什么</b>：字段是最小的数据单元，决定最终输出表格的列。</p>") +

                _("<p><b>示例</b>：<code>标题</code> → <code>h1.article-title</code>、<code>日期</code> → <code>time.published</code></p>") +

                _("<hr><p style='color:gray;font-size:11px'>💡 可选的 <b>AI 辅助设计</b> 能根据描述自动推荐字段规则。</p>")
            ),
            3: (
                _("<h3>📥 Step 4：下载设置</h3>") +

                _("<p><b>是什么</b>：配置附件下载和文件类型过滤。</p>") +

                _("<p><b>为什么</b>：控制是否下载 PDF/图片/文档，避免下载不必要的大文件。</p>") +

                _("<p><b>示例</b>：限制扩展名 <code>.pdf,.docx</code> 或最大文件大小 50MB</p>") +

                _("<hr><p style='color:gray;font-size:11px'>💡 下载文件默认存储在 <code>output/downloads/</code> 子目录。</p>")
            ),
            4: (
                _("<h3>✅ Step 5：预览与确认</h3>") +

                _("<p><b>是什么</b>：在正式运行前检查所有配置，运行小样本试跑。</p>") +

                _("<p><b>为什么</b>：避免配置错误导致的大规模失败——小样本验证是安全底线。</p>") +

                _("<p><b>示例</b>：先采集 5 页，确认字段正确后再全量运行。</p>") +

                _("<hr><p style='color:gray;font-size:11px'>💡 始终建议先试跑再全量执行。</p>")
            ),
        }
        text = tips.get(page_id, "")
        self._wizard_info_panel.setText(text)

    def _sync_wizard_to_editor(self) -> None:
        self._yaml_editor.update_from_config(self._config)

    def _on_editor_sync_to_form(self, config: CrawlConfig) -> None:
        if self._updating_wizard:
            return
        self._updating_wizard = True
        self._config = config
        self._rebuild_wizard()
        self._updating_wizard = False
        ToastManager.instance().success(_("已从编辑器同步到表单"))

    def _on_editor_config_changed(self) -> None:
        pass

    # ================================================================
    #  运行前检查与小样本试跑
    # ================================================================

    def _show_preflight(self) -> None:
        if not self._config_path:
            self._save_config_as()
        if not self._config_path:
            return
        self._save_config()
        # S3.1.1：预检移入后台线程，避免冻结界面
        from .core.background_worker import BackgroundWorker, run_worker

        config_path = self._config_path

        class _PreflightWorker(BackgroundWorker):
            def __init__(self, path: str, parent=None) -> None:
                super().__init__(parent)
                self._config_path = path

            def work(self) -> dict:
                return run_preflight(load_core_config(self._config_path))

        self._preflight_pending = True
        run_worker(
            _PreflightWorker(str(config_path)),
            on_succeeded=self._apply_preflight,
            on_failed=self._on_preflight_failed,
        )

    def _on_preflight_failed(self, error: str) -> None:
        QMessageBox.warning(self, _("运行前检查失败"), error)
        self._preflight_pending = False

    def _apply_preflight(self, report: dict) -> None:
        self._preflight_pending = False
        lines = []
        icons = {"ok": "✓", "warning": "!", "error": "×"}
        for check in report["checks"]:
            lines.append(f"{icons.get(check['status'], '·')} {check['title']}：{check['message']}")
        estimate = report["estimate"]
        lines.extend([
            "",
            _(f"资源模式：{estimate['resource_profile']['name']}"),
            _(f"预计最低运行时间：{estimate['estimated_minimum_seconds']} 秒"),
            _(f"预计原始数据空间：约 {estimate['estimated_raw_storage_mb']} MB"),
        ])
        box = QMessageBox(self)
        box.setWindowTitle(_("运行前检查"))
        box.setIcon(QMessageBox.Icon.Information if report["ok"] else QMessageBox.Icon.Warning)
        box.setText(_("检查通过，可以先用 3 页小样本验证。") if report["ok"] else _("检查发现阻止运行的问题。"))
        box.setDetailedText("\n".join(lines))
        sample_button = box.addButton(_("试跑 3 页"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(_("关闭"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() == sample_button and report["ok"]:
            self._start_sample_run()

    def _start_sample_run(self) -> None:
        if not self._config_path:
            return
        thread = QThread(self)
        worker = SampleRunWorker(self._config_path, 3)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def completed(result: dict) -> None:
            if self._close_after_background_jobs:
                thread.quit()
                return
            sample = result.get("sample", {})
            QMessageBox.information(
                self, _("小样本试跑完成"),
                _(f"状态：{sample.get('status')}\n处理页面：{sample.get('processed', 0)}\n") +

                _(f"提取记录：{sample.get('records', 0)}\n报告：{result.get('report', '')}"),
            )
            thread.quit()

        def failed(message: str) -> None:
            if self._close_after_background_jobs:
                thread.quit()
                return
            QMessageBox.warning(self, _("小样本试跑失败"), message)
            thread.quit()

        worker.finished.connect(completed)
        worker.failed.connect(failed)
        thread.finished.connect(lambda: self._finish_sample_job(thread, worker))
        self._sample_jobs.append((thread, worker))
        thread.start()
        ToastManager.instance().info(_("正在独立工作区试跑 3 页，不会改变正式任务断点"))

    def _finish_sample_job(self, thread: QThread, worker: SampleRunWorker) -> None:
        self._sample_jobs = [job for job in self._sample_jobs if job != (thread, worker)]
        worker.deleteLater()
        thread.deleteLater()
        self._finish_deferred_close_if_safe()

    # ================================================================
    #  对话框
    # ================================================================

    def _open_error_center(self) -> None:
        workspace = Path(self._config.workspace).expanduser()
        if not workspace.is_absolute():
            workspace = self._project_root / workspace
        path = workspace / "output" / "error_center.html"
        if path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.resolve())))
        else:
            QMessageBox.information(self, _("错误中心"), _("当前项目还没有错误中心报告；完成一次任务后会自动生成。"))

    def _show_run_comparison(self) -> None:
        workspace = Path(self._config.workspace).expanduser()
        if not workspace.is_absolute():
            workspace = self._project_root / workspace
        database = workspace / "state.sqlite3"
        if not database.is_file():
            QMessageBox.information(self, _("运行对比"), _("当前项目还没有可对比的运行记录。"))
            return
        with StateStore(database) as state:
            rows = state.rows(
                "SELECT run_id, started_at, status FROM runs ORDER BY started_at DESC LIMIT 30"
            )
            if len(rows) < 2:
                QMessageBox.information(self, _("运行对比"), _("至少完成两次运行后才能进行对比。"))
                return
            labels = [f"{row['started_at']} · {row['status']} · {row['run_id']}" for row in rows]
            before_label, ok = QInputDialog.getItem(self, _("运行对比"), _("选择较早的一次运行："), labels, 1, False)
            if not ok:
                return
            after_label, ok = QInputDialog.getItem(self, _("运行对比"), _("选择较新的一次运行："), labels, 0, False)
            if not ok:
                return
            before_id = rows[labels.index(before_label)]["run_id"]
            after_id = rows[labels.index(after_label)]["run_id"]
            if before_id == after_id:
                QMessageBox.warning(self, _("运行对比"), _("请选择两次不同的运行。"))
                return
            report = compare_runs(state, str(before_id), str(after_id))
        output = workspace / "output" / f"run_comparison_{str(before_id)[:8]}_{str(after_id)[:8]}.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        QMessageBox.information(
            self, _("运行对比完成"),
            _(f"新增：{report['added']}\n修改：{report['modified']}\n") +

            _(f"确认删除：{report['removed']}\n可能删除：{report['possibly_removed']}\n\n报告：{output}"),
        )

    def _manage_plugins(self) -> None:
        directory = self._project_root / "plugins"
        directory.mkdir(parents=True, exist_ok=True)
        inspections = inspect_directory(directory)
        dialog = QDialog(self)
        dialog.setWindowTitle(_("插件管理与权限"))
        dialog.resize(760, 460)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(_("插件启用前只做静态检查；所需权限会明确列出，不会自动批准。")))
        listing = QListWidget(dialog)
        configured = self._config.passthrough.get("plugins", {})
        current_paths = configured.get("paths", []) if isinstance(configured, dict) else []
        current_resolved = {
            str((self._project_root / str(path)).resolve()) if not Path(str(path)).is_absolute() else str(Path(str(path)).resolve())
            for path in current_paths
        }
        for inspection in inspections:
            state = _("兼容") if inspection.compatible else _("不可用")
            permissions = ", ".join(inspection.permissions) or _("无额外权限")
            item = QListWidgetItem(_(f"{inspection.name} {inspection.version} · {state} · 权限: {permissions}"))
            item.setData(Qt.ItemDataRole.UserRole, inspection)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            checked = inspection.path in current_resolved and inspection.compatible
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
            item.setToolTip(inspection.description + ("\n" + "\n".join(inspection.errors) if inspection.errors else ""))
            if not inspection.compatible:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
            listing.addItem(item)
        layout.addWidget(listing)
        open_button = QPushButton(_("打开插件目录"))
        open_button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory))))
        layout.addWidget(open_button)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        selected = []
        requested_permissions: set[str] = set()
        for row in range(listing.count()):
            row_item = listing.item(row)
            assert row_item is not None
            if row_item.checkState() != Qt.CheckState.Checked:
                continue
            inspection = row_item.data(Qt.ItemDataRole.UserRole)
            selected.append(str(Path(inspection.path).resolve().relative_to(self._project_root.resolve())))
            requested_permissions.update(inspection.permissions)
        if requested_permissions:
            answer = QMessageBox.question(
                self, _("批准插件权限"),
                _("所选插件请求以下权限：\n\n") + "\n".join(sorted(requested_permissions)) + _("\n\n是否批准？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        plugins = self._config.passthrough.setdefault("plugins", {})
        if isinstance(plugins, dict):
            plugins["paths"] = selected
            plugins["approved_permissions"] = sorted(requested_permissions)
        ToastManager.instance().info(_("已启用 {0} 个插件；运行前仍会执行兼容性和权限检查").format(len(selected)))

    def _record_browser_actions(self, initial_url: object = None) -> None:
        default_url = str(initial_url) if isinstance(initial_url, str) else (
            self._config.seed_urls[0] if self._config.seed_urls else "https://"
        )
        if isinstance(initial_url, str):
            url, accepted = default_url, True
        else:
            url, accepted = QInputDialog.getText(
                self, _("学习网页操作"),
                _("填写浏览器地址栏中的入口。打开后请正常搜索、点击下一页或滚动；完成时关闭窗口："),
                text=default_url,
            )
        if not accepted or not url.strip():
            return
        output = self._project_root / "work" / "action_recordings" / f"{self._config.task_id}.yaml"
        output.parent.mkdir(parents=True, exist_ok=True)
        self._recorder_thread = QThread(self)
        self._recorder_worker = ActionRecorderWorker(url.strip(), output)
        self._recorder_worker.moveToThread(self._recorder_thread)
        self._recorder_thread.started.connect(self._recorder_worker.run)

        def completed(result: dict) -> None:
            if self._close_after_background_jobs:
                if self._recorder_thread is not None:
                    self._recorder_thread.quit()
                return
            browser = self._config.passthrough.setdefault("browser", {})
            if isinstance(browser, dict):
                browser["actions"] = result.get("actions", [])
                browser["headless"] = True
            self._config.source_kind = "browser"
            if url.strip() not in self._config.seed_urls:
                self._config.seed_urls.insert(0, url.strip())
            self._rebuild_wizard()
            ToastManager.instance().success(_("网页操作已写入当前任务；密码值已替换为 secret:// 引用"))
            QMessageBox.information(
                self, _("录制完成"),
                _(f"已记录 {len(result.get('actions', []))} 个操作并写入当前配置。\n") +

                _("密码不会明文保存，请在运行前配置 browser_password 密钥。"),
            )
            if self._recorder_thread is not None:
                self._recorder_thread.quit()

        def failed(message: str) -> None:
            if self._close_after_background_jobs:
                if self._recorder_thread is not None:
                    self._recorder_thread.quit()
                return
            QMessageBox.warning(self, _("录制失败"), message)
            if self._recorder_thread is not None:
                self._recorder_thread.quit()

        self._recorder_worker.finished.connect(completed)
        self._recorder_worker.failed.connect(failed)
        self._recorder_thread.finished.connect(
            lambda thread=self._recorder_thread, worker=self._recorder_worker:
            self._finish_recorder_job(thread, worker)
        )
        self._recorder_thread.start()
        ToastManager.instance().info(_("正在录制网页操作；完成后关闭录制浏览器窗口"))

    def _finish_recorder_job(
        self, thread: QThread | None, worker: ActionRecorderWorker | None,
    ) -> None:
        if self._recorder_thread is thread:
            self._recorder_thread = None
        if self._recorder_worker is worker:
            self._recorder_worker = None
        if worker is not None:
            worker.deleteLater()
        if thread is not None:
            thread.deleteLater()
        self._finish_deferred_close_if_safe()

    def _manage_schedules(self) -> None:
        from ..runtime.scheduler import ScheduleStore

        dialog = QDialog(self)
        dialog.setWindowTitle(_("定时任务"))
        dialog.resize(680, 420)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(_(
            _("任务保存在本地；请用系统计划任务定期执行 omnicrawl schedule run-due。")
        )))
        schedule_list = QListWidget(dialog)
        layout.addWidget(schedule_list)

        form = QFormLayout()
        interval = QSpinBox(dialog)
        interval.setRange(1, 10080)
        interval.setValue(60)
        interval.setSuffix(_(" 分钟"))
        form.addRow(_("运行间隔"), interval)
        # CalendarPopup 接入：选择首次运行日期
        start_row = QHBoxLayout()
        start_date_label = QLineEdit()
        start_date_label.setReadOnly(True)
        start_date_label.setPlaceholderText(_("立即开始（点击选择日期）"))
        start_row.addWidget(start_date_label)
        start_date_btn = QPushButton("📅")
        start_date_btn.setFixedWidth(36)
        start_date_btn.setToolTip(_("选择首次运行日期"))

        def _pick_date() -> None:
            from .widgets.calendar_popup import CalendarPopup
            popup = CalendarPopup(dialog)
            # A15：走公开信号 date_selected，不再访问私有 _calendar
            popup.date_selected.connect(start_date_label.setText)
            popup.exec()

        start_date_btn.clicked.connect(_pick_date)
        start_row.addWidget(start_date_btn)
        form.addRow(_("首次运行"), start_row)
        require_ac = QCheckBox(_("仅接通电源时运行"), dialog)
        require_ac.setChecked(True)
        form.addRow(_("电源条件"), require_ac)
        require_network = QCheckBox(_("需要可用网络接口"), dialog)
        require_network.setChecked(True)
        form.addRow(_("网络条件"), require_network)
        minimum_battery = QSpinBox(dialog)
        minimum_battery.setRange(0, 100)
        minimum_battery.setValue(30)
        minimum_battery.setSuffix("%")
        form.addRow(_("最低电量"), minimum_battery)
        layout.addLayout(form)

        buttons = QHBoxLayout()
        add_button = QPushButton(_("添加当前配置"), dialog)
        toggle_button = QPushButton(_("启用/停用选中任务"), dialog)
        close_button = QPushButton(_("关闭"), dialog)
        buttons.addWidget(add_button)
        buttons.addWidget(toggle_button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        database = self._project_root / "work" / "schedules.sqlite3"

        def refresh() -> None:
            schedule_list.clear()
            with ScheduleStore(database) as store:
                for value in store.list():
                    state = _("启用") if value["enabled"] else _("停用")
                    minutes = max(1, int(value["interval_seconds"]) // 60)
                    item = QListWidgetItem(
                        f"[{state}] {value['name']} — {minutes} {_('分钟')} — {value['config_path']}"
                    )
                    item.setData(Qt.ItemDataRole.UserRole, value["schedule_id"])
                    item.setData(Qt.ItemDataRole.UserRole + 1, bool(value["enabled"]))
                    schedule_list.addItem(item)

        def add_current() -> None:
            if not self._config_path:
                self._save_config_as()
            if not self._config_path:
                return
            self._save_config()
            try:
                with ScheduleStore(database) as store:
                    store.add(
                        self._config_path.stem,
                        self._config_path,
                        interval.value() * 60,
                        conditions={
                            "require_ac": require_ac.isChecked(),
                            "require_network": require_network.isChecked(),
                            "minimum_battery_percent": minimum_battery.value(),
                        },
                    )
            except Exception as exc:
                QMessageBox.critical(dialog, _("添加失败"), str(exc))
                return
            refresh()

        def toggle_current() -> None:
            item = schedule_list.currentItem()
            if item is None:
                QMessageBox.information(dialog, _("提示"), _("请先选择一个任务。"))
                return
            try:
                with ScheduleStore(database) as store:
                    store.set_enabled(
                        str(item.data(Qt.ItemDataRole.UserRole)),
                        not bool(item.data(Qt.ItemDataRole.UserRole + 1)),
                    )
            except Exception as exc:
                QMessageBox.critical(dialog, _("更新失败"), str(exc))
                return
            refresh()

        add_button.clicked.connect(add_current)
        toggle_button.clicked.connect(toggle_current)
        close_button.clicked.connect(dialog.accept)
        refresh()
        dialog.exec()

    def _show_template_library(self) -> None:
        templates = self._template_loader.discover_templates(force=True)
        if not templates:
            QMessageBox.information(self, _("模板库"), _("未找到任何模板"))
            return
        dialog = TemplateLibraryDialog(templates, self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_template:
            template = dialog.selected_template
            try:
                from ..templates.template_application import apply_template

                recipe = yaml.safe_load(template.filepath.read_text(encoding="utf-8")) or {}
                current = yaml.safe_load(to_yaml(self._config)) or {}
                application = apply_template(current, recipe)
            except Exception as exc:
                QMessageBox.warning(self, _("模板不可用"), str(exc))
                return
            preview = "\n".join(
                f"{item.get('business_section', item['path'])}: " +

                f"{item.get('business_change', item['change_type'])} — " +

                f"{item['path']}"
                for item in application.changes[:18]
            )
            box = QMessageBox(self)
            box.setWindowTitle(_("组合模板"))
            box.setText(_("将模板能力组合到当前任务，不覆盖名称、入口网址、主题、已有字段和输出选择。"))
            box.setInformativeText(template.why or template.description)
            box.setDetailedText(preview or _("没有需要修改的配置"))
            box.setStandardButtons(QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Cancel)
            if box.exec() != QMessageBox.StandardButton.Apply:
                return
            self._config = from_yaml(yaml.safe_dump(application.after, allow_unicode=True, sort_keys=False))
            self._config_path = None
            self._config_label.setText(_("组合模板: ") + template.display_name)
            self._rebuild_wizard()
            if self._config.has_placeholders():
                # B14：模板组合后存在 {{...}} 占位符需显式警告，避免带着未替换占位符直接运行
                QMessageBox.warning(
                    self, _("存在占位符"),
                    _("模板包含尚未替换的占位符（{{...}}），直接运行可能采集不到数据，请先在编辑器中替换。"),
                )
            else:
                ToastManager.instance().success(_("模板能力已组合；建议先试跑 3 页"))

    # ================================================================
    #  站点智能识别
    # ================================================================

    def _inspect_site(self, url: str) -> None:
        self._config_wizard.step2_page.set_inspecting(True)
        self._statusbar.showMessage(_("正在安全探测网址并识别模板…"))
        thread = QThread(self)
        worker = SiteInspectionWorker(url, self._config.task_intent)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_site_inspected)
        worker.failed.connect(self._on_site_inspection_failed)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(lambda: self._finish_inspection_job(thread, worker))
        self._inspection_jobs.append((thread, worker))
        thread.start()

    def _finish_inspection_job(self, thread: QThread, worker: SiteInspectionWorker) -> None:
        self._inspection_jobs = [job for job in self._inspection_jobs if job != (thread, worker)]
        worker.deleteLater()
        thread.deleteLater()
        if not self._close_after_background_jobs:
            self._config_wizard.step2_page.set_inspecting(False)
        self._finish_deferred_close_if_safe()

    def _on_site_inspection_failed(self, message: str) -> None:
        if self._close_after_background_jobs:
            return
        ToastManager.instance().error(_("智能识别失败"))
        QMessageBox.warning(
            self, _("智能识别失败"),
            _("无法安全完成探测。原配置没有改变。\n\n") + message,
        )

    def _on_site_inspected(self, report: dict, url: str) -> None:
        if self._close_after_background_jobs:
            return
        recommendations = list(report.get("recommendations", []))
        lines = [
            _("页面类型: ") + str(report.get("page_type", "unknown")),
            _("动态页面: ") + (_("是") if report.get("dynamic") else _("否")),
            _("CMS: ") + (", ".join(report.get("cms", [])) or _("未识别")),
            _("分页: ") + (", ".join(report.get("pagination", [])) or _("未识别")),
            "",
            _("推荐模板:"),
        ]
        catalog = bundled_template_catalog([self._project_root / "templates"])
        for index, item in enumerate(recommendations[:5], 1):
            record = catalog.get(str(item.get("id", "")))
            name = record.metadata.name if record else str(item.get("id"))
            reasons = "、".join(str(value) for value in item.get("reasons", []))
            lines.append(_(f"  {index}. {name} — {reasons or '安全通用回退'}"))
        if not recommendations:
            QMessageBox.information(self, _("智能识别结果"), "\n".join(lines))
            return
        best_id = str(recommendations[0].get("id", ""))
        reply = QMessageBox.question(
            self, _("智能识别结果"),
            "\n".join(lines) + "\n\n" + _("是否套用第一项模板？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        record = catalog.get(best_id)
        if record is None:
            QMessageBox.warning(self, _("模板不可用"), best_id)
            return
        origin = QUrl(url).adjusted(QUrl.UrlFormattingOption.RemovePath).toString().rstrip("/")
        values = {}
        for key in record.metadata.placeholders:
            lowered = key.casefold()
            if lowered == "site_url":
                values[key] = origin
            elif "url" in lowered:
                values[key] = url
        rendered = catalog.render(record, values, strict=False)
        current = yaml.safe_load(to_yaml(self._config)) or {}
        composed = compose_recipe(current, rendered)
        changes = diff_config(current, composed)
        preview = []
        for change in changes[:18]:
            preview.append(f"{change.path}: {change.before!r} → {change.after!r}")
        if len(changes) > 18:
            preview.append(_(f"…另有 {len(changes) - 18} 项"))
        confirm = QMessageBox(self)
        confirm.setWindowTitle(_("组合建议预览"))
        confirm.setText(_("模板只会补充所需能力；任务名称、入口网址、主题、已有字段和输出选择将保留。"))
        confirm.setInformativeText(
            (record.metadata.why or record.metadata.description)
            + (_(f"\n限制：{record.metadata.limitations}") if record.metadata.limitations else "")
        )
        confirm.setDetailedText("\n".join(preview) or _("没有需要修改的配置"))
        confirm.setStandardButtons(QMessageBox.StandardButton.Apply | QMessageBox.StandardButton.Cancel)
        if confirm.exec() != QMessageBox.StandardButton.Apply:
            return
        self._config = from_yaml(yaml.safe_dump(composed, allow_unicode=True, sort_keys=False))
        self._config_path = None
        self._config_label.setText(_("智能模板: ") + record.metadata.name)
        self._rebuild_wizard()
        ToastManager.instance().info(_("智能模板已加载；请检查红色占位符后运行"))

    # ================================================================
    #  历史记录
    # ================================================================

    def _load_history_config(self, config_path: str) -> None:
        try:
            self._config = load_yaml(Path(config_path))
            self._config_path = Path(config_path)
            self._config_label.setText(self._config_path.name)
            self._rebuild_wizard()
            ToastManager.instance().success(_("历史配置已加载"))
        except Exception as e:
            QMessageBox.critical(self, _("加载失败"), str(e))

    def _load_history_results(self, workspace: str) -> None:
        # A14：workspace 可能含 ~ 等用户目录标记，需 expanduser 后判断绝对路径
        ws_path = Path(workspace).expanduser()
        if not ws_path.is_absolute():
            ws_path = self._project_root / ws_path
        csv_path = next(
            (path for path in (ws_path / "output" / "records.csv", ws_path / "records.csv") if path.is_file()),
            None,
        ) if ws_path.is_dir() else None
        if csv_path:
            self._result_table.load_csv(csv_path)
            self._chart_view.load_csv(csv_path)
            files = next(
                (path for path in (ws_path / "artifacts", ws_path / "downloads") if path.is_dir()),
                ws_path,
            )
            self._file_list.set_directory(files)
            self._stack.setCurrentIndex(3)

    def _auto_load_results(self) -> None:
        # A14：workspace 可能含 ~ 等用户目录标记，需 expanduser 后判断绝对路径
        workspace = Path(self._config.workspace).expanduser()
        if not workspace.is_absolute():
            workspace = self._project_root / workspace
        csv_path = next(
            (path for path in (workspace / "output" / "records.csv", workspace / "records.csv") if path.is_file()),
            None,
        )
        if csv_path:
            self._result_table.load_csv(csv_path)
            self._chart_view.load_csv(csv_path)
        download_dir = (
            workspace / "artifacts"
            if (workspace / "artifacts").is_dir()
            else workspace / self._config.download.output_dir
        )
        if download_dir.is_dir():
            self._file_list.set_directory(download_dir)

    def _open_result_folder(self) -> None:
        # A14：workspace 可能含 ~ 等用户目录标记，需 expanduser 后判断绝对路径
        workspace = Path(self._config.workspace).expanduser()
        if not workspace.is_absolute():
            workspace = self._project_root / workspace
        if workspace.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(workspace)))

    @pyqtSlot(str)
    def _on_draft_found(self, draft_path: str) -> None:
        reply = QMessageBox.question(
            self, _("恢复草稿"),
            _("检测到未保存的更改，是否恢复？\n\n草稿文件: {0}").format(draft_path),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            config = self._autosave.load_draft(Path(draft_path))
            if config:
                self._config = config
                self._config_path = None
                self._config_label.setText(_("已恢复的草稿"))
                self._rebuild_wizard()
                ToastManager.instance().success(_("草稿已恢复"))

    # ================================================================
    #  系统托盘
    # ================================================================

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.show()
            self.raise_()
            self.activateWindow()

    # ================================================================
    #  窗口事件
    # ================================================================

    def _background_threads(self) -> list[QThread]:
        """Return unique auxiliary threads that must finish before window teardown."""
        threads = [thread for thread, _worker in self._inspection_jobs]
        threads.extend(thread for thread, _worker in self._sample_jobs)
        if self._recorder_thread is not None:
            threads.append(self._recorder_thread)
        # Views and child dialogs own additional QThread subclasses.  Include
        # every descendant so closing the main window never destroys one while
        # its ``run()`` method is still active.
        threads.extend(self.findChildren(QThread))
        return list(dict.fromkeys(threads))

    def _request_background_shutdown(self) -> None:
        """Ask auxiliary work to stop without deleting a live QThread."""
        for thread in self._background_threads():
            if thread.isRunning():
                thread.requestInterruption()
                thread.quit()

    def _finish_deferred_close_if_safe(self) -> None:
        """Close only after worker-owned QThreads have actually stopped."""
        if not self._close_after_background_jobs:
            return
        if any(thread.isRunning() for thread in self._background_threads()):
            QTimer.singleShot(100, self._finish_deferred_close_if_safe)
            return
        self._close_after_background_jobs = False
        self.close()

    def closeEvent(self, event) -> None:
        if self._tray_icon and self._tray_icon.isVisible() and self._task_runner.is_running:
            reply = QMessageBox.question(
                self, _("确认"),
                _("任务正在运行中。是否最小化到系统托盘？\n\n" +

                  _("选择「否」将终止任务并退出。")),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.hide()
                event.ignore()
                return
        if self._task_runner.is_running:
            # S3.1.5：无托盘图标时不再静默 stop()——给出三选一确认
            box = QMessageBox(self)
            box.setWindowTitle(_("确认退出"))
            box.setText(_("任务正在运行中。关闭窗口将如何处理？"))
            stop_btn = box.addButton(_("停止任务并退出"), QMessageBox.ButtonRole.DestructiveRole)
            hide_btn = box.addButton(_("最小化到后台"), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(_("取消"), QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if clicked == hide_btn:
                self.hide()
                event.ignore()
                return
            if clicked != stop_btn:
                event.ignore()
                return
            self._task_runner.stop()
        if any(thread.isRunning() for thread in self._background_threads()):
            self._close_after_background_jobs = True
            self._request_background_shutdown()
            self._set_status(_("正在安全结束后台操作，完成后将自动退出"))
            QTimer.singleShot(100, self._finish_deferred_close_if_safe)
            event.ignore()
            return
        self._async_manager.cancel_all()
        self._autosave.delete_draft()
        event.accept()

    def _rebuild_wizard(self) -> None:
        old_wizard = self._config_wizard
        self._config_wizard = ConfigWizard(self._config)
        for page_id in self._config_wizard.pageIds():
            page = self._config_wizard.page(page_id)
            if page is not None and hasattr(page, 'config_changed'):
                page.config_changed.connect(self._on_wizard_changed)  # type: ignore[attr-defined]
            if page is not None and hasattr(page, 'inspect_requested'):
                page.inspect_requested.connect(self._inspect_site)  # type: ignore[attr-defined]
            if page is not None and hasattr(page, 'record_requested'):
                page.record_requested.connect(self._record_browser_actions)  # type: ignore[attr-defined]
        self._connect_wizard_actions()
        # A20：重建后重连 currentIdChanged（原 815 行仅初始连接一次，重建后信息面板不再更新）
        self._config_wizard.currentIdChanged.connect(self._update_wizard_info_panel)
        # S3.1.2：在 wizard_splitter 上替换旧向导（原代码操作外层 wizard_layout，
        # 新向导从未真正显示）
        index = self._wizard_splitter.indexOf(old_wizard)
        if index >= 0:
            self._wizard_splitter.replaceWidget(index, self._config_wizard)
        else:  # pragma: no cover - 防御：splitter 找不到时回退旧路径
            layout = self._wizard_widget.layout()
            if layout is not None:
                layout.removeWidget(old_wizard)
                layout.addWidget(self._config_wizard)
        old_wizard.deleteLater()
        if hasattr(self, "_resource_profile_combo"):
            for index in range(self._resource_profile_combo.count()):
                if self._resource_profile_combo.itemData(index) == self._config.resource_profile:
                    self._resource_profile_combo.blockSignals(True)
                    self._resource_profile_combo.setCurrentIndex(index)
                    self._resource_profile_combo.blockSignals(False)
                    break
        self._apply_ui_mode(self._settings.ui_mode)

    def _set_status(self, text: str) -> None:
        self._statusbar.showMessage(text, 5000)
        ToastManager.instance().info(text)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.isLocalFile():
                    path = Path(url.toLocalFile())
                    if path.suffix.lower() in (".yaml", ".yml", ".csv", ".zip"):
                        event.acceptProposedAction()
                        return
        event.ignore()

    def dropEvent(self, event) -> None:
        for url in event.mimeData().urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            suffix = path.suffix.lower()
            if suffix in (".yaml", ".yml"):
                try:
                    self._config = load_yaml(path)
                    self._config_path = path
                    self._config_label.setText(path.name)
                    self._rebuild_wizard()
                    ToastManager.instance().success(_("配置已加载: {0}").format(path.name))
                except Exception as e:
                    QMessageBox.critical(self, _("加载失败"), str(e))
            elif suffix == ".csv":
                self._result_table.load_csv(path)
                self._chart_view.load_csv(path)
                self._stack.setCurrentIndex(3)
                ToastManager.instance().success(_("结果文件已加载: {0}").format(path.name))
            elif suffix == ".zip":
                self._import_config_package_from_path(path)
        event.acceptProposedAction()

    # ================================================================
    #  Delegate 引用的辅助方法
    # ================================================================

    def _start_demo(self) -> None:
        """EnvironmentChecker 引用 — 转发到离线演示。"""
        self._create_offline_demo()

    def _open_template_browser(self) -> None:
        """EnvironmentChecker 引用 — 转发到模板库。"""
        self._show_template_library()

    def _show_pdf_region_dialog(self) -> None:
        """MenuBuilder 引用 — 打开 PDF 区域选择对话框。"""
        PdfRegionSelectorDialog(self).exec()

    def _open_ai_service_center(self) -> None:
        """MenuBuilder 引用 — 打开 AI 服务中心对话框，写 .env 文件。"""
        from .views.ai_service_center import AIServiceCenterDialog

        ai_config = self._load_ai_config_from_env()
        dialog = AIServiceCenterDialog(ai_config, self, workspace=self._project_root)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._save_ai_config_to_env(ai_config)

    def _show_stealth_settings(self) -> None:
        """打开反检测与隐身设置对话框。"""
        from .views.stealth_settings import StealthSettingsDialog

        dialog = StealthSettingsDialog(self._settings, parent=self)
        dialog.exec()

    def _on_monitor_desktop_notify(self, title: str, message: str) -> None:
        """变更监控触发桌面通知 — 通过系统托盘弹窗。"""
        if self._tray_icon and self._tray_icon.isSystemTrayAvailable():
            self._tray_icon.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 8000)

    def _load_ai_config_from_env(self) -> dict[str, Any]:
        """从单一真源 .env 加载 AI 配置（优先级 os.environ > 项目 .env > 用户级 .env）。"""
        env_vars = load_ai_env(self._project_root)
        config: dict[str, Any] = {"mode": "disabled"}
        provider = env_vars.get("OMNICRAWL_AI_PROVIDER", "disabled")
        if provider != "disabled":
            config["mode"] = "enabled"
            config["default_provider"] = "default"
            config.setdefault("providers", {})["default"] = {
                "type": provider,
                "base_url": env_vars.get("OMNICRAWL_AI_BASE_URL", ""),
                "model": env_vars.get("OMNICRAWL_AI_MODEL", ""),
                "api_key": env_vars.get("OMNICRAWL_AI_API_KEY", ""),
                "timeout_seconds": _safe_int(env_vars.get("OMNICRAWL_AI_TIMEOUT"), 60),
            }
        # C36：合并旁路 JSON 中的隐私/预算/路由/抽取设置（保留 .env 解析出的 api_key，
        # 绝不信任旁路文件里的 api_key）
        sidecar = load_ai_config_sidecar(self._project_root)
        if sidecar:
            sidecar_providers = sidecar.get("providers", {})
            if isinstance(sidecar_providers, dict) and "default" in sidecar_providers:
                sidecar_providers["default"].pop("api_key", None)
            for key, value in sidecar.items():
                if key == "api_key":
                    continue
                config[key] = value
        return config

    def _save_ai_config_to_env(self, config: dict[str, Any]) -> None:
        """将 AI 配置行级写入单一真源 .env，并同步进程内 os.environ。

        保留 .env 中的注释/空行/顺序；关闭 AI 时同步删除 KEY/BASE_URL/MODEL。
        S2.2.2：OMNICRAWL_AI_API_KEY 明文先加密入 secrets_store，.env 只写
        ``secret://`` 引用（引用幂等，不可存时拒绝写入绝不回退明文）。
        """
        updates: dict[str, str | None] = {}
        if config.get("mode") == "disabled":
            updates["OMNICRAWL_AI_PROVIDER"] = "disabled"
            updates["OMNICRAWL_AI_BASE_URL"] = None
            updates["OMNICRAWL_AI_MODEL"] = None
            updates["OMNICRAWL_AI_API_KEY"] = None
            updates["OMNICRAWL_AI_TIMEOUT"] = None
        else:
            provider = config.get("providers", {}).get("default", {})
            api_key = str(provider.get("api_key", "")).strip()
            try:
                if api_key:
                    api_key = seal_secret("ai.env.OMNICRAWL_AI_API_KEY", api_key)
            except Exception:
                QMessageBox.warning(
                    self,
                    _("无法安全保存"),
                    _("API key 无法加密存储（secrets_store 不可用），已拒绝写入明文，请检查系统凭据库或设置 {var}。").format(
                        var="OMNICRAWL_MASTER_PASSWORD"
                    ),
                )
                return
            updates["OMNICRAWL_AI_PROVIDER"] = provider.get("type", "openai_compatible")
            updates["OMNICRAWL_AI_BASE_URL"] = provider.get("base_url", "")
            updates["OMNICRAWL_AI_MODEL"] = provider.get("model", "")
            updates["OMNICRAWL_AI_API_KEY"] = api_key
            updates["OMNICRAWL_AI_TIMEOUT"] = str(provider.get("timeout_seconds", 60))
        save_ai_env(updates, project_root=self._project_root)
        sync_ai_env_to_os(updates)
        # C36：将 .env 无法承载的隐私/预算/路由/抽取设置持久化到旁路 JSON（不含明文 api_key）
        save_ai_config_sidecar(self._project_root, config)


def _safe_int(value: Any, default: int) -> int:
    """解析整数，非数字值回退默认（避免 .env 脏数据使 AI 静默不可用）。"""
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return default


# ================================================================
#  入口函数
# ================================================================

def main() -> int:
    """应用主入口。

    Returns:
        退出码：0 成功，1 失败。
    """
    # S3.1.7：运行时环境配置从模块顶层迁入 main()——import gui.main 无副作用
    configure_runtime_environment()

    parser = argparse.ArgumentParser(
        prog="omnicrawler-gui",
        description=_("OmniCrawler GUI 工作台 — 可视化爬虫配置与管理工具"),
    )
    parser.add_argument(
        "--run", type=str, default=None,
        help=_("无 GUI 模式：直接执行指定的 YAML 配置文件"),
    )
    parser.add_argument(
        "--headless", action="store_true",
        help=_("无 GUI 模式（与 --run 等效，不显示图形界面）"),
    )
    parser.add_argument(
        "--log-level", type=str, default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help=_("日志级别（默认: INFO）"),
    )

    args = parser.parse_args()

    if args.run or args.headless:
        config_path = args.run
        if not config_path:
            print(_("错误: --headless 模式下需要指定 --run <yaml_path>"), file=sys.stderr)
            return 1
        from .runner.headless_runner import run_headless
        return run_headless(config_path=config_path, log_level=args.log_level)

    try:
        configure_logging(args.log_level, log_format="text")

        if is_frozen():
            settings_path = portable_data_root() / ".omnicrawler" / "settings"
            settings_path.mkdir(parents=True, exist_ok=True)
            QSettings.setDefaultFormat(QSettings.Format.IniFormat)
            QSettings.setPath(
                QSettings.Format.IniFormat,
                QSettings.Scope.UserScope,
                str(settings_path),
            )
        app = QApplication(sys.argv)
        app.setApplicationName("OmniCrawler GUI")
        app.setOrganizationName("OmniCrawler")
        app.setApplicationVersion(GUI_VERSION)

        window = MainWindow()
        window.show()

        def _global_exception_hook(exc_type, exc_value, exc_tb):
            traceback.print_exception(exc_type, exc_value, exc_tb)
            if hasattr(window, '_show_error_dialog'):
                window._show_error_dialog(exc_value)
            sys.__excepthook__(exc_type, exc_value, exc_tb)

        sys.excepthook = _global_exception_hook
        return app.exec()

    except Exception as e:
        print(_(f"严重错误: {e}"), file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
