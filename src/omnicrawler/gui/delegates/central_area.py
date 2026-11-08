"""中央区域面板装配委托。

``MainWindow._setup_central_area`` 原为 226 行装配代码（左侧导航 + 14 页页面栈 +
全部页面信号接线），是 MainWindow 里最长的方法。批次 6-4b 按
``docs/MAINWINDOW_REFACTOR.md`` §3 第②条把它整块平移到本委托：**不改任何
connect 目标、不改装配顺序**，主窗口只保留组合顺序与跨面板协调。

约定：委托对主窗口的一切访问都写作 ``self._mw.X``（见 ``_base.py``）；本模块
不持有控件引用，所有控件仍挂在主窗口属性上，供其它 delegate 与测试取用。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..design_system import PageTransitionController
from ..home import HomePage
from ..i18n import _
from ..navigation import NavIndex
from ..views.change_monitor import ChangeMonitorView
from ..views.chart_view import ChartView
from ..views.convert_tool import ConvertView
from ..views.developer_inspector import DeveloperInspector
from ..views.file_list import FileList
from ..views.pdf_workbench import PdfWorkbenchView
from ..views.plugin_market import PluginMarketView
from ..views.professional_review import EvidenceView
from ..views.result_table import ResultTable
from ..views.yaml_editor import YamlEditor
from ..widgets.log_console import LogConsole
from ..widgets.status_indicator import StatusIndicator
from ._base import _BaseDelegate


class CentralAreaBuilder(_BaseDelegate):
    """装配中央区域：左侧导航 + 页面栈 + 页面信号接线。"""

    def setup(self) -> None:
        """构建导航与页面栈，并接好全部页面信号（批次 6-4b 整块平移）。"""
        mw = self._mw
        central = QWidget()
        mw.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        mw._nav = QListWidget()
        mw._nav.setObjectName("mainNavigation")
        mw._nav.setAccessibleName(_("主导航"))
        mw._nav.setFixedWidth(190)

        nav_items: list[tuple[str, int | None]] = [
            (_("工作"), None),
            ("⌂ " + _("首页"), 4),
            ("⚙ " + _("任务工作台"), 0),
            ("▶ " + _("运行与历史"), 2),
            ("📊 " + _("结果与复核"), 3),
            (_("自动化"), None),
            ("🔔 " + _("变更监控"), 8),
            (_("工具"), None),
            ("📄 " + _("PDF 工作台"), 6),
            ("🔁 " + _("格式互转"), 7),
            ("🎯 " + _("场景与模板"), 11),
            ("🔐 " + _("登录会话"), 12),
            ("🪟 " + _("市场橱窗（QML 试点）"), 13),
            (_("高级"), None),
            ("📝 " + _("YAML 编辑器"), 1),
            ("🔍 " + _("证据查看器"), 5),
            ("🧩 " + _("插件市场"), 9),
            ("🛠 " + _("开发者检查器"), 10),
        ]
        mw._nav_pages = {}
        for row, (label, page) in enumerate(nav_items):
            item = QListWidgetItem(label)
            if page is None:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, _("导航分组"))
            else:
                mw._nav_pages[row] = page
            mw._nav.addItem(item)

        mw._nav.currentRowChanged.connect(mw._on_nav_changed)
        main_layout.addWidget(mw._nav)

        mw._stack = QStackedWidget()

        mw._workspace_widget = QWidget()
        workspace_layout = QVBoxLayout(mw._workspace_widget)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        mw._advanced_summary = QLabel("")
        mw._advanced_summary.setWordWrap(True)
        mw._advanced_summary.setAccessibleName(_("已启用高级规则摘要"))
        mw._advanced_summary.setObjectName("advancedSummary")
        mw._advanced_summary.setProperty("status", "warning")
        workspace_layout.addWidget(mw._advanced_summary)

        # P0：任务工作台（Task Canvas）
        from ..views.task_canvas import TaskCanvas

        mw._task_canvas = TaskCanvas(mw._config, project_root=str(mw._project_root))
        workspace_layout.addWidget(mw._task_canvas)
        workspace_layout.addWidget(mw._task_canvas.persistent_action_bar())
        mw._stack.addWidget(mw._workspace_widget)

        # P0：画布信号接线（保存/试跑/运行/查看 YAML）
        mw._task_canvas.config_changed.connect(mw._on_workspace_changed)
        mw._task_canvas.save_requested.connect(mw._config_delegate.save_config)
        mw._task_canvas.trial_run_requested.connect(mw._request_trial_run)
        mw._task_canvas.run_requested.connect(mw._request_run)
        mw._task_canvas.yaml_view_requested.connect(mw._open_yaml_view)
        # P2：意图区 URL 探活（600ms 停顿由画布内部调度，这里只负责发起与回填）
        mw._task_canvas.probe_requested.connect(mw._probe_site)

        mw._yaml_editor = YamlEditor()
        mw._yaml_editor.sync_to_form.connect(mw._on_editor_sync_to_form)
        mw._yaml_editor.sync_status.connect(mw._set_status)
        mw._stack.addWidget(mw._yaml_editor)

        monitor_widget = QWidget()
        monitor_layout = QVBoxLayout(monitor_widget)
        monitor_layout.setContentsMargins(8, 8, 8, 8)

        status_layout = QHBoxLayout()
        mw._monitor_status = StatusIndicator(size=16)
        status_layout.addWidget(mw._monitor_status)
        mw._monitor_status_text = QLabel(_("空闲"))
        status_layout.addWidget(mw._monitor_status_text)
        status_layout.addStretch()
        mw._elapsed_label = QLabel("")
        status_layout.addWidget(mw._elapsed_label)
        monitor_layout.addLayout(status_layout)

        mw._progress_bar = QProgressBar()
        mw._progress_bar.setRange(0, 100)
        mw._progress_bar.setValue(0)
        mw._progress_bar.setTextVisible(True)
        monitor_layout.addWidget(mw._progress_bar)

        mw._progress_url_label = QLabel("")
        mw._progress_url_label.setObjectName("muted")
        monitor_layout.addWidget(mw._progress_url_label)

        monitor_layout.addWidget(mw._resource_monitor)

        mw._log_console = LogConsole()
        monitor_layout.addWidget(mw._log_console)

        ctrl_layout = QHBoxLayout()
        ctrl_run = QPushButton(_("▶ 运行"))
        ctrl_run.clicked.connect(mw._request_run)
        ctrl_layout.addWidget(ctrl_run)
        ctrl_stop = QPushButton(_("■ 停止"))
        ctrl_stop.clicked.connect(mw._run_delegate.stop_task)
        ctrl_layout.addWidget(ctrl_stop)
        ctrl_pause = QPushButton(_("Ⅱ 暂停/继续"))
        ctrl_pause.clicked.connect(mw._run_delegate.toggle_pause)
        ctrl_layout.addWidget(ctrl_pause)
        ctrl_layout.addStretch()
        monitor_layout.addLayout(ctrl_layout)

        mw._task_history.load_history()
        monitor_layout.addWidget(mw._task_history)
        mw._stack.addWidget(monitor_widget)

        results_widget = QWidget()
        results_layout = QVBoxLayout(results_widget)
        results_layout.setContentsMargins(8, 8, 8, 8)
        mw._result_table = ResultTable()
        mw._result_table.record_selected_for_review.connect(mw._on_record_selected_for_review)
        results_layout.addWidget(mw._result_table)
        mw._file_list = FileList()
        results_layout.addWidget(mw._file_list)
        mw._chart_view = ChartView()
        results_layout.addWidget(mw._chart_view)
        mw._stack.addWidget(results_widget)

        mw._home = HomePage(project_root=str(mw._project_root))
        mw._home.quick_task_ready.connect(mw._apply_quick_task)
        mw._home.natural_task_ready.connect(mw._apply_natural_task)
        mw._home.open_workspace.connect(lambda: mw._nav.setCurrentRow(NavIndex.WORKSPACE))
        mw._home.open_recent.connect(lambda: mw._nav.setCurrentRow(NavIndex.MONITOR))
        mw._home.open_recent_config.connect(mw._load_history_config)
        mw._home.open_recent_results.connect(mw._load_history_results)
        mw._home.set_recent_tasks(mw._task_history.recent_records())
        mw._task_history.history_changed.connect(
            lambda: mw._home.set_recent_tasks(mw._task_history.recent_records())
        )
        # S3.1.2：修复"结果与复核"错页（原误用 NavIndex.MONITOR）
        mw._home.open_results.connect(lambda: mw._nav.setCurrentRow(NavIndex.RESULTS))
        # §A-27：首页 PDF 指引 → 真跳转，并把需求原文交给 PDF 工作台
        mw._home.open_pdf_workbench.connect(mw._open_pdf_workbench)
        mw._home.open_schedule.connect(mw._manage_schedules)
        mw._home.import_task.connect(mw._config_delegate.import_config_package)
        mw._home.run_doctor.connect(mw._env_checker.recheck_env)
        mw._home.create_demo.connect(mw._create_offline_demo)
        mw._home.create_starter.connect(mw._create_starter)
        mw._home.open_getting_started.connect(mw._show_getting_started)
        # B-4 ConvertX：首页按钮跳到格式互转面板（NavIndex.CONVERT_TOOL）
        mw._home.open_convert_tool.connect(
            lambda: mw._nav.setCurrentRow(NavIndex.CONVERT_TOOL)
        )
        # S4 场景管理：首页按钮跳到场景面板（NavIndex.SCENE）
        mw._home.open_scene.connect(
            lambda: mw._nav.setCurrentRow(NavIndex.SCENE)
        )
        # 运行对比：首页按钮复用菜单既有入口（review/run_compare）
        mw._home.open_run_compare.connect(mw._show_run_comparison)
        mw._stack.addWidget(mw._home)

        mw._evidence_view = EvidenceView(workspace=Path(mw._config.workspace).expanduser())
        mw._evidence_view.back_to_results.connect(lambda: mw._nav.setCurrentRow(NavIndex.RESULTS))  # 返回结果与复核页
        mw._stack.addWidget(mw._evidence_view)
        mw._pdf_workbench = PdfWorkbenchView()
        mw._stack.addWidget(mw._pdf_workbench)
        # B-4：ConvertX 格式互转工具页（stack index 7）
        mw._convert_tool = ConvertView()
        mw._convert_tool.open_output_folder_requested.connect(
            lambda p: QDesktopServices.openUrl(QUrl.fromLocalFile(p))
        )
        mw._stack.addWidget(mw._convert_tool)

        # A3：变更监控复用共享探活 AsyncFetcher（惰性构建，走 EgressBroker 审计）
        if mw._probe_fetcher is None:
            try:
                mw._probe_fetcher = mw._build_probe_fetcher()
            except Exception:  # noqa: BLE001 — 构建失败静默降级，监控回退 urllib
                mw._probe_fetcher = None
        mw._change_monitor = ChangeMonitorView(settings=mw._settings, fetcher=mw._probe_fetcher)
        mw._change_monitor.desktop_notify.connect(mw._chrome.on_monitor_desktop_notify)
        mw._stack.addWidget(mw._change_monitor)

        mw._plugin_market = PluginMarketView(
            project_root=mw._project_root,
            app_config=mw._market_app_config(),
        )
        mw._plugin_market.installation_completed.connect(mw._on_market_plugin_installed)
        mw._plugin_market.activation_requested.connect(mw._activate_market_plugin)
        mw._plugin_market.deactivation_requested.connect(mw._deactivate_market_plugin)
        mw._plugin_market.uninstall_completed.connect(mw._on_market_plugin_uninstalled)
        mw._plugin_market.set_enabled_plugins(mw._configured_market_plugin_ids())
        mw._stack.addWidget(mw._plugin_market)

        mw._developer_inspector = DeveloperInspector(mw._config, mw._project_root)
        mw._stack.addWidget(mw._developer_inspector)

        # S4：场景管理面板（懒加载 SceneStore，workspace/scene.sqlite3）
        from ..views.scene_panel import ScenePanel

        mw._scene_panel = ScenePanel(Path(mw._config.workspace).expanduser())
        mw._stack.addWidget(mw._scene_panel)

        # U3：登录会话独立导航页（工具分组）—— headed 登录窗口 + 会话列表
        mw._login_session_view = mw._login_session.setup()
        mw._stack.addWidget(mw._login_session_view)

        # Q1：QML 试点页（仅新增页面；不建 delegate，避免再往 GUI 大环里加一条边）
        from ..views.qml_showcase import QmlShowcaseView

        mw._qml_showcase = QmlShowcaseView(mw._project_root)
        mw._stack.addWidget(mw._qml_showcase)

        main_layout.addWidget(mw._stack)
        mw._page_transition = PageTransitionController(
            mw._stack, reduced_motion=mw._settings.reduced_motion,
        )
        mw._nav.setCurrentRow(NavIndex.HOME)
