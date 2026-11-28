"""中央区域面板装配委托。

``MainWindow._setup_central_area`` 原为 226 行装配代码（左侧导航 + 14 页页面栈 +
全部页面信号接线），是 MainWindow 里最长的方法。批次 6-4b 按
``docs/MAINWINDOW_REFACTOR.md`` §3 第②条把它整块平移到本委托：**不改任何
connect 目标、不改装配顺序**，主窗口只保留组合顺序与跨面板协调。

约定：委托对主窗口的一切访问都写作 ``self._mw.X``（见 ``_base.py``）；本模块
不持有控件引用，所有控件仍挂在主窗口属性上，供其它 delegate 与测试取用。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..design_system import PageTransitionController
from ..home import HomePage
from ..i18n import _
from ..navigation import NavIndex, NavKey, visible_entries
from ..views.change_monitor import ChangeMonitorView
from ..views.chart_view import ChartView
from ..views.developer_inspector import DeveloperInspector
from ..views.file_list import FileList
from ..views.market import MarketView
from ..views.professional_review import EvidenceView
from ..views.result_table import ResultTable
from ..views.yaml_editor import YamlEditor
from ..widgets.log_console import LogConsole
from ..widgets.status_indicator import StatusIndicator
from ._base import _BaseDelegate

LOGGER = logging.getLogger(__name__)


def _goto_nav_key(window: Any, nav_key: str, label: str) -> None:
    """按 **NavKey** 跳到对应页面；该页不存在时给可操作提示。

    为什么不直接用 `NavIndex.X`：那是**行号**，由 `visible_entries()`
    派生，导航项增删就会变。实测未装 document-workbench 时
    `NavIndex.CONVERT_TOOL` 指向的是"登录会话"页——点了会跳错地方。
    key 不会变，找不到就是真的不存在。
    """
    from ...core.capabilities import navigation_capabilities
    from ..navigation import visible_entries

    # 与导航构建**同一判据**（`central_area` 里 `_build_nav` 用的就是它）：
    # 能力由 `navigation_capabilities()` 现场算，不是一个缓存属性。
    keys = [entry.key for entry in visible_entries(navigation_capabilities())]
    if nav_key not in keys:
        QMessageBox.information(
            window,
            _("功能未安装"),
            _("{label} 由插件 document-workbench 提供，当前未安装。\n\n"
              "可在「OmniCrawler 市场」中安装后使用。").format(label=label),
        )
        return
    window._nav.setCurrentRow(keys.index(nav_key))


def _load_pdf_views() -> Any:
    """按需加载 PDF 工作台视图（来自插件 document-workbench）。

    迁移后（2026-11-17）：PDF 工作台已迁成插件，宿主不再持有其 GUI 代码。
    这里经插件 facade 加载视图模块；插件未安装时返回 None，
    调用方据此跳过页面构造（导航入口也不会出现）。

    **为什么不在宿主保留一份**：那等于把 PDF 工作台的 GUI 又搬回来，
    用户明确要求"原始代码里看不到这个 PDF 工作台的痕迹"。
    """
    from ...services import document_facade

    if not document_facade.document_capability()["available"]:
        return None
    try:
        return document_facade.pdf_views()
    except Exception:  # noqa: BLE001 - 视图加载失败不应让主界面起不来
        LOGGER.warning(_("PDF 工作台视图加载失败（插件存在但不可用）"), exc_info=True)
        return None

#: 导航项 key → 展示标签。**必须经 _() 包裹**（i18n 门禁按此判据）。
#: 标签放在展示层而不是 navigation.py：那个模块不依赖 Qt 与 i18n，
#: 这样它才能在无 Qt 环境下被测试与工具直接引用。
_NAV_LABELS: dict[str, str] = {
    NavKey.GROUP_WORK: _("工作"),
    NavKey.HOME: "⌂ " + _("首页"),
    NavKey.WORKSPACE: "⚙ " + _("任务工作台"),
    NavKey.MONITOR: "▶ " + _("运行与历史"),
    NavKey.RESULTS: "📊 " + _("结果与复核"),
    NavKey.GROUP_AUTOMATION: _("自动化"),
    NavKey.CHANGE_MONITOR: "🔔 " + _("变更监控"),
    NavKey.GROUP_TOOLS: _("工具"),
    NavKey.PDF_WORKBENCH: "📄 " + _("文档工作台"),
    NavKey.CONVERT_TOOL: "🔁 " + _("格式互转"),
    NavKey.SCENE: "🎯 " + _("场景与模板"),
    NavKey.LOGIN_SESSION: "🔐 " + _("登录会话"),
    NavKey.QML_SHOWCASE: "🪟 " + _("市场橱窗（QML 试点）"),
    NavKey.GROUP_ADVANCED: _("高级"),
    NavKey.YAML_EDITOR: "📝 " + _("YAML 编辑器"),
    NavKey.EVIDENCE: "🔍 " + _("证据查看器"),
    NavKey.PLUGIN_MARKET: "🧩 " + _("OmniCrawler 市场"),
    NavKey.DEVELOPER: "🛠 " + _("开发者检查器"),
}


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

        nav_sidebar = QWidget()
        nav_sidebar.setObjectName("navSidebar")
        nav_sidebar.setAccessibleName(_("左侧导航栏"))
        nav_sidebar.setFixedWidth(190)
        nav_sidebar_layout = QVBoxLayout(nav_sidebar)
        nav_sidebar_layout.setContentsMargins(0, 0, 0, 0)
        nav_sidebar_layout.setSpacing(0)

        quick_jump_btn = QPushButton(_("🔍 快速跳转 (Ctrl+K)"))
        quick_jump_btn.setObjectName("quickJumpButton")
        quick_jump_btn.setAccessibleName(_("快速跳转与命令面板"))
        quick_jump_btn.setToolTip(_("打开全局命令面板与页面快速切换 (Ctrl+K)"))
        quick_jump_btn.clicked.connect(self.open_command_palette)
        nav_sidebar_layout.addWidget(quick_jump_btn)
        mw.__dict__["_command_palette_btn"] = quick_jump_btn

        mw._nav = QListWidget()
        mw._nav.setObjectName("mainNavigation")
        mw._nav.setAccessibleName(_("主导航"))
        mw._nav.setFixedWidth(190)

        # 条目 6：导航由 NAV_LAYOUT 派生，并按**能力就绪状态**过滤。
        # 未装 PDF 就不显示 PDF 工作台——用户不该看到一个点进去空空的工作台。
        # 行号因此是运行期算出来的，所以身份必须用 key（见 main._goto_nav）。
        from ...core.capabilities import navigation_capabilities

        entries = visible_entries(navigation_capabilities())
        mw._nav_pages = {}
        row_by_key: dict[str, int] = {}
        key_by_row: dict[int, str] = {}
        for row, entry in enumerate(entries):
            item = QListWidgetItem(_NAV_LABELS[entry.key])
            row_by_key[entry.key] = row
            key_by_row[row] = entry.key
            if entry.stack_key is None:
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, _("导航分组"))
            mw._nav.addItem(item)

        mw._nav_row_by_key = row_by_key
        mw._nav_key_by_row = key_by_row
        nav_sidebar_layout.addWidget(mw._nav, 1)
        main_layout.addWidget(nav_sidebar)

        mw._stack = QStackedWidget()
        stack_index_by_key: dict[str, int] = {}

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
        stack_index_by_key[NavKey.WORKSPACE] = mw._stack.addWidget(mw._workspace_widget)

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
        stack_index_by_key[NavKey.YAML_EDITOR] = mw._stack.addWidget(mw._yaml_editor)

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
        stack_index_by_key[NavKey.MONITOR] = mw._stack.addWidget(monitor_widget)

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
        stack_index_by_key[NavKey.RESULTS] = mw._stack.addWidget(results_widget)

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
        # B-4 ConvertX：首页按钮跳到格式互转面板。
        #
        # **不能用 `NavIndex.CONVERT_TOOL`**：那是**行号**，随导航项增删而变。
        # 未装 document-workbench 时该行实际指向 `login_session`（实测：
        # `CONVERT_TOOL=9` → 无插件时是"登录会话"页），点了会跳到错误页面。
        # 改为按 **key** 定位，找不到就给可操作提示。
        mw._home.open_convert_tool.connect(
            lambda: _goto_nav_key(mw, NavKey.CONVERT_TOOL, _("格式互转"))
        )
        # S4 场景管理：首页按钮按 key 跳到场景面板（不依赖静态行号）
        mw._home.open_scene.connect(
            lambda: _goto_nav_key(mw, NavKey.SCENE, _("场景与模板"))
        )
        # 运行对比：首页按钮复用菜单既有入口（review/run_compare）
        mw._home.open_run_compare.connect(mw._show_run_comparison)
        stack_index_by_key[NavKey.HOME] = mw._stack.addWidget(mw._home)

        mw._evidence_view = EvidenceView(workspace=Path(mw._config.workspace).expanduser())
        mw._evidence_view.back_to_results.connect(lambda: mw._nav.setCurrentRow(NavIndex.RESULTS))  # 返回结果与复核页
        stack_index_by_key[NavKey.EVIDENCE] = mw._stack.addWidget(mw._evidence_view)

        # 迁移后（2026-11-17）：PDF 工作台与格式互转已迁成插件 document-workbench。
        # 视图改为**按插件可用性条件构造**：
        # * 装了 ⇒ 从插件目录加载视图类，行为与迁移前一致；
        # * 没装 ⇒ 不创建视图，导航入口也不显示（`NavEntry.requires` 已声明 "pdf"）。
        #
        # 为什么不在宿主里保留占位页：用户明确要求"不要看到一个空荡荡的
        # PDF 工作台界面"。没有就是没有，不摆空壳。
        _pdf_views = _load_pdf_views()
        if _pdf_views is not None:
            mw._pdf_workbench = _pdf_views.DocumentWorkbenchView()
            stack_index_by_key[NavKey.PDF_WORKBENCH] = mw._stack.addWidget(mw._pdf_workbench)
            # B-4：ConvertX 格式互转工具页
            mw._convert_tool = _pdf_views.ConvertView()
            mw._convert_tool.open_output_folder_requested.connect(
                lambda p: QDesktopServices.openUrl(QUrl.fromLocalFile(p))
            )
            stack_index_by_key[NavKey.CONVERT_TOOL] = mw._stack.addWidget(mw._convert_tool)
        else:
            # 未装插件：这两个页面不存在。属性置 None，调用方据此判空。
            mw._pdf_workbench = None
            mw._convert_tool = None

        # A3：变更监控复用共享探活 AsyncFetcher（惰性构建，走 EgressBroker 审计）
        if mw._probe_fetcher is None:
            try:
                mw._probe_fetcher = mw._build_probe_fetcher()
            except Exception:  # noqa: BLE001 — 构建失败静默降级，监控回退 urllib
                mw._probe_fetcher = None
        mw._change_monitor = ChangeMonitorView(settings=mw._settings, fetcher=mw._probe_fetcher)
        mw._change_monitor.desktop_notify.connect(mw._chrome.on_monitor_desktop_notify)
        stack_index_by_key[NavKey.CHANGE_MONITOR] = mw._stack.addWidget(mw._change_monitor)

        mw._plugin_market = MarketView(
            project_root=mw._project_root,
            app_config=mw._market_app_config(),
        )
        mw._plugin_market.installation_completed.connect(mw._on_market_plugin_installed)
        mw._plugin_market.activation_requested.connect(mw._activate_market_plugin)
        mw._plugin_market.deactivation_requested.connect(mw._deactivate_market_plugin)
        mw._plugin_market.uninstall_completed.connect(mw._on_market_plugin_uninstalled)
        mw._plugin_market.set_enabled_plugins(mw._configured_market_plugin_ids())
        stack_index_by_key[NavKey.PLUGIN_MARKET] = mw._stack.addWidget(mw._plugin_market)

        mw._developer_inspector = DeveloperInspector(mw._config, mw._project_root)
        stack_index_by_key[NavKey.DEVELOPER] = mw._stack.addWidget(mw._developer_inspector)

        # S4：场景管理面板（懒加载 SceneStore，workspace/scene.sqlite3）
        from ..views.scene_panel import ScenePanel

        mw._scene_panel = ScenePanel(Path(mw._config.workspace).expanduser())
        stack_index_by_key[NavKey.SCENE] = mw._stack.addWidget(mw._scene_panel)

        # U3：登录会话独立导航页（工具分组）—— headed 登录窗口 + 会话列表
        mw._login_session_view = mw._login_session.setup()
        stack_index_by_key[NavKey.LOGIN_SESSION] = mw._stack.addWidget(mw._login_session_view)

        # Q1：QML 试点页（仅新增页面；不建 delegate，避免再往 GUI 大环里加一条边）
        from ..views.qml_showcase import QmlShowcaseView

        mw._qml_showcase = QmlShowcaseView(mw._project_root)
        stack_index_by_key[NavKey.QML_SHOWCASE] = mw._stack.addWidget(mw._qml_showcase)

        for row, entry in enumerate(entries):
            if entry.stack_key is not None and entry.stack_key in stack_index_by_key:
                mw._nav_pages[row] = stack_index_by_key[entry.stack_key]

        # ★ W6-A（2026-11-28）：登记「内置 key → 页面对象」。
        #
        #   插件导航页会往侧栏**末尾**插行；插入后 `row → 栈索引` 的映射必须
        #   整体重算，否则内置条目会指向错页 —— 那正是历史上的「结果与复核错页」。
        #   重算需要「key → 页面对象」，而那时手上只有「row → 栈索引」，
        #   所以在这里一次性固化下来。
        #
        #   放在 `_nav_pages` 建好之后：之前这些页面还没全部 addWidget。
        from .plugin_nav_page import adopt_builtin_nav_pages

        adopt_builtin_nav_pages(mw)

        main_layout.addWidget(mw._stack)
        mw._page_transition = PageTransitionController(
            mw._stack, reduced_motion=mw._settings.reduced_motion,
        )
        mw._nav.currentRowChanged.connect(mw._on_nav_changed)
        mw._nav.setCurrentRow(row_by_key.get(NavKey.HOME, NavIndex.HOME))

    def open_command_palette(self) -> Any:
        """打开 Ctrl+K 全局命令面板与快速跳转对话框。"""
        from ..command_palette import CommandPaletteDialog

        dialog = CommandPaletteDialog(self._mw, nav_labels=_NAV_LABELS)
        dialog.exec()
        return dialog
