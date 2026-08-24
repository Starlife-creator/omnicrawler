"""OmniCrawler GUI 工作台 — 主入口。

启动 Application 主窗口，支持 --headless/--run 无 GUI 模式参数。

架构说明：MainWindow 是组合根，通过 8 个 delegate 类分发功能：
  MenuBuilder / ToolbarManager / ThemeManager / ErrorDialogHelper
  EnvironmentChecker / HelpDialogManager / RunDelegate / ConfigDelegate
每个 delegate 用 ``__getattr__`` 透明转发到 MainWindow 属性。
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
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

LOGGER = logging.getLogger(__name__)


def _cli_mode() -> bool:
    """S3.1.7：只读判定 CLI 模式（无副作用）。"""
    return any(arg in ("--headless", "--run") for arg in sys.argv[1:])


_GUI_APP_HOLD = None

if not _cli_mode():
    try:
        from PySide6.QtCore import (
            QObject,
            Qt,
            QThread,
            QTimer,
            QUrl,
            Signal,
            Slot,
        )
        from PySide6.QtGui import (
            QAction,
            QDesktopServices,
        )
        from PySide6.QtWidgets import (
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
            QStackedWidget,
            QStatusBar,
            QSystemTrayIcon,
            QVBoxLayout,
            QWidget,
        )
    except ImportError as e:
        print(_(f"PySide6 未安装，无法启动图形界面: {e}"), file=sys.stderr)
        print(_("请运行: pip install omnicrawler-platform[gui]"), file=sys.stderr)
        sys.exit(1)

    # 冷启动提速：低频功能（AI 设置/插件管理/运行对比）的重型依赖在
    # 使用点函数内懒导入（ai_env≈70ms/plugin_inspector≈53ms/run_compare≈26ms）
    from ..core.config import load_config as load_core_config
    from ..pipeline_ops.preflight import run_preflight, run_sample
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
    from .views.convert_tool import ConvertView  # B-4：ConvertX 格式互转面板
    from .views.developer_inspector import DeveloperInspector
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

    GUI_VERSION = APP_VERSION


def _thread_interrupted() -> bool:
    """Qt may return ``None`` before a QObject has entered its worker thread."""
    thread = QThread.currentThread()
    return thread is not None and thread.isInterruptionRequested()


class SiteInspectionWorker(QObject):
    finished = Signal(object, str)
    failed = Signal(str)

    def __init__(self, url: str, intent: str = "", robots_fail_closed: bool = True, fetcher: Any | None = None) -> None:
        super().__init__()
        self.url = url
        self.intent = intent
        # P2-8：探测/检测交由调用方从用户配置传入，替代硬编码 True
        self.robots_fail_closed = robots_fail_closed
        # P2：探活复用共享 AsyncFetcher（内部经 EgressBroker 审计出网）
        self.fetcher = fetcher

    @Slot()
    def run(self) -> None:
        try:
            if _thread_interrupted():
                return
            report = inspect_url(
                self.url, bundled_template_catalog(), intent=self.intent,
                robots_fail_closed=self.robots_fail_closed, fetcher=self.fetcher,
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
        from .settings import make_qsettings

        self._settings = make_qsettings("OmniCrawler", "GUIWorkbench")
        stored_favorites = self._settings.value("templates/favorites", [])
        if isinstance(stored_favorites, str):
            stored_favorites = [stored_favorites]
        # QSettings.value 返回 object：显式收窄为 list 后再迭代
        favorites = stored_favorites if isinstance(stored_favorites, list) else []
        self._favorites = {str(value) for value in favorites}

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
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, url: str, output: Path) -> None:
        super().__init__()
        self._url = url
        self._output = output

    @Slot()
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
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, config_path: Path, pages: int = 3) -> None:
        super().__init__()
        self._config_path = config_path
        self._pages = pages

    @Slot()
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
    _omnicrawler_available: bool
    _omnicrawler_path: str
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

        # ---- 插件信任询问器（本地创作者签名未入信任列表时弹窗确认） ----
        from ..plugins.plugins import (
            TrustPromptResult,
            set_default_trust_prompter,
        )

        def _gui_trust_prompter(
            plugin_id: str, username: str, fingerprint: str
        ) -> TrustPromptResult:
            from PySide6.QtWidgets import QMessageBox

            box = QMessageBox(self)
            box.setWindowTitle(_("信任确认"))
            box.setText(
                _("插件 {0} 的作者 {1}（公钥指纹 {2}）不在本地信任列表。\n\n信任后该作者后续发布的插件将自动加载。").format(
                    plugin_id, username, fingerprint
                )
            )
            trust_button = box.addButton(
                _("信任并加载"), QMessageBox.ButtonRole.AcceptRole
            )
            once_button = box.addButton(
                _("仅本次加载"), QMessageBox.ButtonRole.AcceptRole
            )
            reject_button = box.addButton(
                _("拒绝"), QMessageBox.ButtonRole.RejectRole
            )
            box.setDefaultButton(reject_button)
            box.exec()
            clicked = box.clickedButton()
            if clicked is trust_button:
                return TrustPromptResult.TRUST_AND_LOAD
            if clicked is once_button:
                return TrustPromptResult.LOAD_ONCE
            return TrustPromptResult.REJECT

        set_default_trust_prompter(_gui_trust_prompter)

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
        self._omnicrawler_path = resolve_cli_command(self._settings.omnicrawler_path)
        if self._omnicrawler_path != self._settings.omnicrawler_path:
            self._settings.omnicrawler_path = self._omnicrawler_path
        self._omnicrawler_available = False

        # ---- 核心组件（S3.1.27：抽方法，切换项目可重建）----
        self._build_project_components()

        # ---- 运行状态 ----
        self._task_start_time: datetime | None = None
        self._task_elapsed_timer: QTimer | None = None
        self._inspection_jobs: list[tuple[QThread, SiteInspectionWorker]] = []
        self._sample_jobs: list[tuple[QThread, SampleRunWorker]] = []
        self._probe_jobs: list[tuple[QThread, SiteInspectionWorker]] = []
        self._recorder_thread: QThread | None = None
        self._recorder_worker: ActionRecorderWorker | None = None
        # P2：意图区 URL 探活共享抓取器（懒创建，关闭时释放）
        self._probe_fetcher: Any | None = None
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
        self._install_plugin_ui()

        # ---- 连接信号 ----
        self._setup_global_shortcuts()
        ToastManager.instance().bind(self)
        QTimer.singleShot(100, self._on_first_launch)

    def _maybe_show_identity_welcome(self) -> None:
        """首启检查：无本地身份时引导创建（插件生态签名身份）。"""
        try:
            from .views.identity_welcome import maybe_show_identity_welcome

            maybe_show_identity_welcome(self)
        except Exception:  # noqa: BLE001 - 引导失败不影响使用
            pass

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
        self._maybe_show_identity_welcome()
        self._maybe_show_canvas_welcome_tip()

    def _maybe_show_canvas_welcome_tip(self) -> None:
        """P3：首启只讲 1 点（PRD §3.1）——无任何历史任务时画布气泡提示。"""
        if self._has_saved_tasks():
            return
        self._task_canvas.maybe_show_welcome_tip()

    def _has_saved_tasks(self) -> bool:
        """是否有已保存任务（配置历史目录存在非空快照）。无法判断时保守不打扰。"""
        try:
            root = self._config_history.root
            return root.exists() and any(root.rglob("*.yaml"))
        except Exception:  # noqa: BLE001
            return True

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

    @Slot(str, str)
    def _on_log_line(self, message: str, level: str) -> None:
        self._run_delegate.on_log_line(message, level)

    @Slot(int, str)
    def _on_progress(self, percent: int, url: str) -> None:
        self._run_delegate.on_progress(percent, url)

    @Slot(str)
    def _on_task_state_changed(self, state: str) -> None:
        self._run_delegate.on_task_state_changed(state)

    @Slot(str, int)
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

    def _install_plugin_ui(self) -> None:
        """装配完成后加载本地插件并挂载 UI 注册（主题/动作/面板/状态栏）。

        插件来源：项目根下 plugins/ 与 plugins_installed/（核心配置默认路径）。
        strict 策略下未签名/未信任插件拒绝加载；加载失败 fail-open，不阻塞启动。
        """
        from ..core.config import DEFAULTS, AppConfig, deep_merge
        from ..pipeline import build_registry
        from ..plugins.plugins import Registry
        from .plugin_host import install_plugin_ui as _install_plugin_ui

        try:
            raw = deep_merge({}, DEFAULTS)
            raw["project"] = {
                "name": "gui",
                "workspace": str(self._project_root / "work"),
            }
            config = AppConfig(
                Path("<gui>"), self._project_root, raw, self._project_root
            )
            registry = build_registry(config)
        except Exception as exc:  # noqa: BLE001 - 插件问题不阻塞 GUI 启动
            LOGGER.warning("GUI startup plugin load failed: %s", exc)
            registry = Registry()
        try:
            errors = _install_plugin_ui(self, registry)
            if errors:
                self._plugin_ui_errors = errors
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Plugin UI install failed: %s", exc)

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
            ("🔁 " + _("格式互转"), 7),    # B-4：ConvertX 面板
            ("📝 " + _("YAML 编辑器"), 1),
            ("📋 " + _("任务监控"), 2),
            ("📊 " + _("结果与复核"), 3),
            ("🔍 " + _("证据查看器"), 5),
            ("🎯 " + _("场景管理"), 11),    # S4：场景/槽位/基因/候选
            ("🔔 " + _("变更监控"), 8),
            ("🧩 " + _("插件市场"), 9),
            ("🛠 " + _("开发者检查器"), 10),
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

        # P0：任务画布替换五步向导（Task Canvas）
        from .views.task_canvas import TaskCanvas

        self._task_canvas = TaskCanvas(self._config, project_root=str(self._project_root))
        wizard_layout.addWidget(self._task_canvas)
        self._stack.addWidget(self._wizard_widget)

        # P0：画布信号接线（保存/试跑/运行/查看 YAML）
        self._task_canvas.config_changed.connect(self._on_wizard_changed)
        self._task_canvas.save_requested.connect(self._save_config)
        self._task_canvas.trial_run_requested.connect(self._request_trial_run)
        self._task_canvas.run_requested.connect(self._request_run)
        self._task_canvas.yaml_view_requested.connect(self._open_yaml_view)
        # P2：意图区 URL 探活（600ms 停顿由画布内部调度，这里只负责发起与回填）
        self._task_canvas.probe_requested.connect(self._probe_site)

        self._yaml_editor = YamlEditor()
        self._yaml_editor.sync_to_form.connect(self._on_editor_sync_to_form)
        self._yaml_editor.sync_status.connect(self._set_status)
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
        # B-4 ConvertX：首页按钮跳到格式互转面板（NavIndex.CONVERT_TOOL）
        self._home.open_convert_tool.connect(
            lambda: self._nav.setCurrentRow(NavIndex.CONVERT_TOOL)
        )
        # S4 场景管理：首页按钮跳到场景面板（NavIndex.SCENE）
        self._home.open_scene.connect(
            lambda: self._nav.setCurrentRow(NavIndex.SCENE)
        )
        # 运行对比：首页按钮复用菜单既有入口（review/run_compare）
        self._home.open_run_compare.connect(self._show_run_comparison)
        self._stack.addWidget(self._home)

        self._evidence_view = EvidenceView(workspace=Path(self._config.workspace).expanduser())
        self._evidence_view.back_to_results.connect(lambda: self._nav.setCurrentRow(NavIndex.RESULTS))  # 返回结果与复核页
        self._stack.addWidget(self._evidence_view)
        self._pdf_workbench = PdfWorkbenchView()
        self._stack.addWidget(self._pdf_workbench)
        # B-4：ConvertX 格式互转工具页（stack index 7）
        self._convert_tool = ConvertView()
        self._convert_tool.open_output_folder_requested.connect(
            lambda p: QDesktopServices.openUrl(QUrl.fromLocalFile(p))
        )
        self._stack.addWidget(self._convert_tool)

        # A3：变更监控复用共享探活 AsyncFetcher（惰性构建，走 EgressBroker 审计）
        if self._probe_fetcher is None:
            try:
                self._probe_fetcher = self._build_probe_fetcher()
            except Exception:  # noqa: BLE001 — 构建失败静默降级，监控回退 urllib
                self._probe_fetcher = None
        self._change_monitor = ChangeMonitorView(settings=self._settings, fetcher=self._probe_fetcher)
        self._change_monitor.desktop_notify.connect(self._on_monitor_desktop_notify)
        self._stack.addWidget(self._change_monitor)

        self._plugin_market = PluginMarketView(project_root=self._project_root)
        self._stack.addWidget(self._plugin_market)

        self._developer_inspector = DeveloperInspector(self._config, self._project_root)
        self._stack.addWidget(self._developer_inspector)

        # S4：场景管理面板（懒加载 SceneStore，workspace/scene.sqlite3）
        from .views.scene_panel import ScenePanel

        self._scene_panel = ScenePanel(Path(self._config.workspace).expanduser())
        self._stack.addWidget(self._scene_panel)

        main_layout.addWidget(self._stack)
        self._page_transition = PageTransitionController(
            self._stack, reduced_motion=self._settings.reduced_motion,
        )
        self._nav.setCurrentRow(NavIndex.HOME)

    def _build_project_components(self) -> None:
        """S3.1.27：构建/重建依赖项目根的组件（switch_project 时复用）。"""
        self._config_history = ConfigHistory(self._project_root / ".config_history")
        self._task_runner = TaskRunner(
            omnicrawler_path=self._omnicrawler_path,
            project_root=self._project_root,
        )
        self._task_runner.log_line.connect(self._on_log_line)
        self._task_runner.progress.connect(self._on_progress)
        self._task_runner.state_changed.connect(self._on_task_state_changed)
        self._task_runner.task_finished.connect(self._on_task_finished)

        self._autosave = AutosaveManager(self._project_root)
        self._autosave.draft_found.connect(self._on_draft_found)
        self._autosave.save_failed.connect(
            lambda msg: ToastManager.instance().warning(msg)
        )

        self._template_loader = TemplateLoader(
            builtin_dir=package_resource("omnicrawler", "templates"),
            user_dir=self._project_root / "templates",
            additional_builtin_dirs=(package_resource("omnicrawler", "gui", "templates"),),
            additional_user_dirs=(self._project_root / "templates_installed",),  # G4：市场安装模板可被发现
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
        # nav row -> stack page: 首页(4), 配置向导(0), PDF工作台(6), 格式互转(7),
        # YAML编辑器(1), 任务监控(2), 结果复核(3), 证据查看器(5), 场景管理(11),
        # 变更监控(8), 插件市场(9), 开发者检查器(10)
        pages = (4, 0, 6, 7, 1, 2, 3, 5, 11, 8, 9, 10)
        page = pages[index] if 0 <= index < len(pages) else 4
        self._page_transition.show(page)
        if page == 1:
            self._yaml_editor.update_from_config(self._config)
        elif page == 3:
            self._auto_load_results()
        elif page == 5:
            pass  # 证据查看器数据由 record_selected_for_review 信号加载
        elif page == 7:
            pass  # 格式互转：进入即就绪，无需预加载数据
        elif page == 10:
            self._developer_inspector.refresh()
        elif page == 11:
            self._scene_panel.refresh_scenes()  # S4：进入场景面板时刷新

    def _apply_quick_task(self, draft: QuickTaskDraft) -> None:
        self._apply_task_draft(draft)
        self._refresh_canvas()
        self._nav.setCurrentRow(NavIndex.WIZARD)
        self._set_status(_("快速草案已生成：请查看自动决定和修改入口，然后先试跑"))

    def _apply_natural_task(self, draft: NaturalLanguageDraft) -> None:
        """Preserve the user's words and extracted topics when entering the wizard."""
        self._apply_task_draft(draft.task)
        self._config.task_description = draft.request
        if draft.topics:
            self._config.topic_include_any = list(dict.fromkeys(topic for topic in draft.topics if topic.strip()))
        self._refresh_canvas()
        self._nav.setCurrentRow(NavIndex.WIZARD)
        cadence = {"weekly": _("每周"), "daily": _("每天"), "monthly": _("每月"), "manual": _("手动")}
        self._set_status(_("已从自然语言生成草案；建议频率：{0}。请确认画布内容并先试跑。").format(
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
            self._refresh_canvas()
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
            self._toggle_btn.setText(_("⇄ 画布"))

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

    def _sync_wizard_to_editor(self) -> None:
        self._yaml_editor.update_from_config(self._config)

    def _on_editor_sync_to_form(self, config: CrawlConfig) -> None:
        if self._updating_wizard:
            return
        self._updating_wizard = True
        self._config = config
        # P0：外部（YAML 编辑器）编辑 → 画布外部编辑检测（无冲突静默同步，有冲突锁定二选一）
        self._task_canvas.notify_external_edit(config)
        self._updating_wizard = False

    # ================================================================
    #  运行前检查与小样本试跑
    # ================================================================

    def _request_trial_run(self) -> None:
        """画布「先试跑 N 页」：先持久化配置，再用画布设定的页数试跑。"""
        self._save_config()
        if not self._config_path:
            return
        self._start_sample_run(self._task_canvas.trial_pages())

    def _request_run(self) -> None:
        """画布「保存并全量运行」：唯一运行出口，先保存配置再启动任务。"""
        self._save_config()
        if not self._config_path:
            return
        # P1：运行前 field_hash 一致校验（PRD §2.2.3）——试跑通过但字段集已变则拒绝
        if not self._task_canvas.trial_matches_fields():
            QMessageBox.warning(
                self, _("试跑已失效"),
                _("字段已变更，请重新试跑后再全量运行。"),
            )
            return
        self._run_task()

    def _open_yaml_view(self) -> None:
        """画布「查看 YAML」：切到侧栏 YAML 编辑器页并载入当前配置。"""
        self._yaml_editor.update_from_config(self._config)
        self._nav.setCurrentRow(NavIndex.YAML_EDITOR)

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

    def _start_sample_run(self, pages: int | None = None) -> None:
        if not self._config_path:
            return
        pages = 3 if pages is None else pages
        thread = QThread(self)
        worker = SampleRunWorker(self._config_path, pages)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)

        def completed(result: dict) -> None:
            if self._close_after_background_jobs:
                thread.quit()
                return
            sample = result.get("sample", {})
            status = str(sample.get("status") or "")
            processed = int(sample.get("processed", 0) or 0)
            records = int(sample.get("records", 0) or 0)
            ok = bool(status) and status != "failed" and processed > 0
            summary = _(f"状态：{status}\n处理页面：{processed}\n提取记录：{records}")
            # P0：试跑结果回填画布（决定「保存并全量运行」是否可用）
            self._task_canvas.set_trial_result(ok, summary)
            QMessageBox.information(
                self, _("小样本试跑完成"),
                summary,
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
        ToastManager.instance().info(_("正在独立工作区试跑 {0} 页，不会改变正式任务断点").format(pages))

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
            ToastManager.instance().info(_("当前项目还没有错误中心报告；完成一次任务后会自动生成。"))

    def _show_run_comparison(self) -> None:
        from ..review.run_compare import compare_runs

        workspace = Path(self._config.workspace).expanduser()
        if not workspace.is_absolute():
            workspace = self._project_root / workspace
        database = workspace / "state.sqlite3"
        if not database.is_file():
            ToastManager.instance().info(_("当前项目还没有可对比的运行记录。"))
            return
        with StateStore(database) as state:
            rows = state.rows(
                "SELECT run_id, started_at, status FROM runs ORDER BY started_at DESC LIMIT 30"
            )
            if len(rows) < 2:
                ToastManager.instance().info(_("至少完成两次运行后才能进行对比。"))
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
        from ..plugins.plugin_inspector import inspect_directory

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
            self._refresh_canvas()
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
            _("任务保存在本地；请用系统计划任务定期执行 omnicrawler schedule run-due。")
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
            ToastManager.instance().info(_("未找到任何模板"))
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
            self._refresh_canvas()
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
        self._statusbar.showMessage(_("正在安全探测网址并识别模板…"))
        thread = QThread(self)
        worker = SiteInspectionWorker(
            url, self._config.task_intent,
            robots_fail_closed=bool(
                (self._config.passthrough.get("http") or {}).get("robots_fail_closed", True)
            ),
        )
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
        origin = QUrl(url).adjusted(QUrl.UrlFormattingOption.RemovePath).toString().rstrip("/")  # type: ignore[arg-type]  # PySide6 存根枚举别名差异，运行时正确
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
        self._refresh_canvas()
        ToastManager.instance().info(_("智能模板已加载；请检查红色占位符后运行"))

    # ================================================================
    #  P2：意图区 URL 探活（画布徽标，失败静默降级）
    # ================================================================

    def _probe_site(self, url: str) -> None:
        """收到画布探活请求：复用共享 AsyncFetcher（走 EgressBroker 审计）发起探测。"""
        if self._close_after_background_jobs or not url:
            return
        if self._probe_fetcher is None:
            try:
                self._probe_fetcher = self._build_probe_fetcher()
            except Exception as exc:  # noqa: BLE001
                # 探活是增强功能：创建失败静默降级，绝不阻断主流程
                self._task_canvas.set_probe_failed(url, str(exc))
                return
        thread = QThread(self)
        worker = SiteInspectionWorker(
            url, self._config.task_intent,
            robots_fail_closed=bool(
                (self._config.passthrough.get("http") or {}).get("robots_fail_closed", True)
            ),
            fetcher=self._probe_fetcher)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(lambda report, target: self._on_probe_finished(target, report))
        worker.failed.connect(lambda message, target=url: self._on_probe_failed(target, message))
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(lambda: self._finish_probe_job(thread, worker))
        self._probe_jobs.append((thread, worker))
        thread.start()

    def _build_probe_fetcher(self) -> Any:
        """构建探活专用 AsyncFetcher：独立审计链，不污染任务配置。"""
        from ..core.config import DEFAULTS, AppConfig, deep_merge
        from ..fetching.async_fetcher import HTTPXAsyncFetcher

        raw = deep_merge(copy.deepcopy(DEFAULTS), {
            "project": {"name": "gui_url_probe", "workspace": "work/site_inspection"},
            "source": {"kind": "static_html", "seeds": []},
            "http": {
                "timeout_seconds": 20.0,
                "retries": 1,
                "max_response_bytes": 10_000_000,
                "respect_robots": True,
                "robots_fail_closed": True,
            },
        })
        root = self._project_root.resolve()
        config = AppConfig(root / ".omnicrawler-inspector.yaml", root, raw, root / "work" / "site_inspection")
        return HTTPXAsyncFetcher(config)

    def _on_probe_finished(self, url: str, report: dict) -> None:
        if self._close_after_background_jobs:
            return
        self._task_canvas.set_probe_result(url, report)

    def _on_probe_failed(self, url: str, message: str) -> None:
        if self._close_after_background_jobs:
            return
        # 静默降级：仅画布徽标提示，不弹窗、不改配置
        self._task_canvas.set_probe_failed(url, message)

    def _finish_probe_job(self, thread: QThread, worker: SiteInspectionWorker) -> None:
        self._probe_jobs = [job for job in self._probe_jobs if job != (thread, worker)]
        worker.deleteLater()
        thread.deleteLater()
        self._finish_deferred_close_if_safe()

    def _release_probe_fetcher(self) -> None:
        """关闭探活共享抓取器（窗口退出前释放连接池与事件循环）。"""
        if self._probe_fetcher is None:
            return
        try:
            self._probe_fetcher.close()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("关闭探活抓取器失败: %s", exc)  # noqa
        self._probe_fetcher = None

    # ================================================================
    #  历史记录
    # ================================================================

    def _load_history_config(self, config_path: str) -> None:
        try:
            self._config = load_yaml(Path(config_path))
            self._config_path = Path(config_path)
            self._config_label.setText(self._config_path.name)
            self._refresh_canvas()
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

    def _export_markdown(self) -> None:
        """任务完成自动导出结果 Markdown（P1-2 修复）。

        run_controller 在 finished 时无参调用本方法；复用 _auto_load_results 的同款
        workspace 归一化逻辑自动定位 records.csv 并导出一份 records.md。
        失败仅记日志，不弹框、不阻塞事件循环（自动路径）；带保存对话框的手动导出
        仍由 result_table 视图独立承担。
        """
        workspace = Path(self._config.workspace).expanduser()
        if not workspace.is_absolute():
            workspace = self._project_root / workspace
        csv_path = next(
            (path for path in (workspace / "output" / "records.csv", workspace / "records.csv")
             if path.is_file()),
            None,
        )
        if csv_path is None:
            logging.getLogger(__name__).warning(
                _("自动导出 Markdown 跳过：未找到结果文件 records.csv 于 %s"), workspace)
            return

        from omnicrawler.export.markdown_exporter import MarkdownExporter

        from .core.background_worker import BackgroundWorker, run_worker

        filepath = Path(csv_path)
        jsonl = filepath.with_name("records.jsonl")
        target = filepath.with_name("records.md")

        class _AutoMarkdownWorker(BackgroundWorker):
            def work(self) -> str:
                MarkdownExporter.export_results(
                    csv_path=filepath,
                    jsonl_path=jsonl if jsonl.is_file() else None,
                    output_path=target,
                    include_evidence=True,
                )
                return str(target)

        run_worker(
            _AutoMarkdownWorker(),
            on_succeeded=lambda path: self._statusbar.showMessage(
                _("已自动导出 Markdown：{0}").format(path), 6000),
            on_failed=lambda err: logging.getLogger(__name__).warning(_("自动导出 Markdown 失败: %s"), err),
        )

    def _open_result_folder(self) -> None:
        # A14：workspace 可能含 ~ 等用户目录标记，需 expanduser 后判断绝对路径
        workspace = Path(self._config.workspace).expanduser()
        if not workspace.is_absolute():
            workspace = self._project_root / workspace
        if workspace.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(workspace)))

    @Slot(str)
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
                self._refresh_canvas()
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
        threads.extend(thread for thread, _worker in self._probe_jobs)
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
        if self._task_runner.is_running:
            # P2-2/1：合并为单次确认弹窗——有托盘 3 选 1，无托盘 2 选 1（无"隐藏"）
            tray_visible = bool(self._tray_icon and self._tray_icon.isVisible())
            box = QMessageBox(self)
            box.setWindowTitle(_("确认退出"))
            box.setText(_("任务正在运行中。关闭窗口将如何处理？"))
            stop_btn = box.addButton(_("停止任务并退出"), QMessageBox.ButtonRole.DestructiveRole)
            if tray_visible:
                # 有托盘才允许隐藏到后台；无托盘时提供隐藏入口会导致窗口不可恢复（P2-1）
                hide_btn = box.addButton(_("最小化到后台"), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(_("取消"), QMessageBox.ButtonRole.RejectRole)
            box.exec()
            clicked = box.clickedButton()
            if tray_visible and clicked == hide_btn:
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
        self._release_probe_fetcher()
        event.accept()

    def _refresh_canvas(self) -> None:
        """外部配置/模式切换后重载任务画布。"""
        self._task_canvas.load_config(self._config)
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
                    self._refresh_canvas()
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
        from ..core.ai_env import load_ai_config_sidecar, load_ai_env

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
        from ..core.ai_env import save_ai_config_sidecar, save_ai_env, sync_ai_env_to_os
        from ..core.credentials import seal_secret

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

        # 便携设置重定向不再使用 QSettings.setDefaultFormat/setPath：
        # 已证实对 QSettings(org, app) 构造在 Windows 上无效（仍落注册表）。
        # 统一由 gui/settings.make_qsettings 在 is_frozen() 下用带路径构造落
        # 应用数据根 INI（F53 同语义）。
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
