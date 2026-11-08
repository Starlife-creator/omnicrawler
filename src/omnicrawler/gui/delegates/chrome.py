"""Window chrome delegate：状态栏 / 系统托盘 / 帮助中心 / 工作台背景。

批次 6-4d：把 ``MainWindow`` 里四处**只被 ``__init__`` 调用一次**的窗口外壳装配
（状态栏、托盘、帮助中心停靠、工作台背景恢复）整块平移到这里，为 ``__init__``
分段腾出规模空间（ratchet 只降不升，基线逐项顶格）。

边界：委托只负责**建**，不持有控件引用——建出来的部件仍然挂在主窗口上
（``mw._statusbar`` / ``mw._tray_icon`` / ``mw._help_center`` …），因为它们是
跨面板共享的（运行委托写 ``_status_text``、主题委托写 ``_dnd_label``、
配置委托写 ``_config_label``）。委托之间因此不互相持有对方控件。

搬运纪律：**不改任何语句顺序、不改任何调用目标**（除了信号接收者从
``mw._on_tray_activated`` 变为本委托的 ``on_tray_activated``，这是搬迁本身
不可避免的一处，且仅涉及托盘自身）。
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QLabel,
    QMenu,
    QSystemTrayIcon,
)

from ..app_icon import app_icon
from ..help_center import HelpCenterDock
from ..i18n import _
from ..widgets.status_indicator import StatusIndicator
from ._base import _BaseDelegate


class WindowChromeBuilder(_BaseDelegate):
    """Builds the window chrome that is not part of the central area."""

    def setup_status_bar(self) -> None:
        """状态栏：项目路径 / 配置路径 / 状态指示 / 状态文本 / DND / 完成提示。"""
        mw = self._mw
        sb = mw.statusBar()
        assert sb is not None
        mw._statusbar = sb
        mw._project_label = QLabel()
        mw._project_label.setObjectName("muted")
        mw._env_checker.update_project_label()
        mw._statusbar.addWidget(mw._project_label)
        mw._config_label = QLabel(_("未保存"))
        mw._config_label.setObjectName("muted")
        mw._statusbar.addWidget(mw._config_label)
        mw._status_indicator = StatusIndicator(size=12)
        mw._statusbar.addPermanentWidget(mw._status_indicator)
        mw._status_text = QLabel(_("空闲"))
        mw._statusbar.addPermanentWidget(mw._status_text)
        mw._dnd_label = QLabel()
        mw._dnd_label.setVisible(mw._dnd_mode)
        mw._statusbar.addPermanentWidget(mw._dnd_label)
        mw._theme_manager.update_dnd_label()
        mw._finish_label = QLabel()
        mw._finish_label.setObjectName("muted")
        mw._statusbar.addPermanentWidget(mw._finish_label)

    def setup_system_tray(self) -> None:
        """系统托盘图标与右键菜单；无托盘的环境下把 ``_tray_icon`` 置 None。"""
        mw = self._mw
        if QSystemTrayIcon.isSystemTrayAvailable():
            mw._tray_icon = QSystemTrayIcon(mw)
            # B1：托盘用带牌图标（自带不透明底，深浅托盘对比度都达标，见《优化方案》§九附录 A §3.5）
            mw._tray_icon.setIcon(app_icon())
            mw._tray_icon.setToolTip(_("OmniCrawler GUI 工作台"))
            mw._tray_icon.activated.connect(self.on_tray_activated)
            # S3.1.2：QMenu(mw) 接管所有权，托盘右键菜单不因父对象销毁而悬空
            tray_menu = QMenu(mw)
            show_action = QAction(_("显示主窗口"), mw)
            show_action.triggered.connect(mw.show)
            tray_menu.addAction(show_action)
            update_action = QAction(_("检查更新"), mw)
            update_action.triggered.connect(mw._check_for_updates)
            tray_menu.addAction(update_action)
            quit_action = QAction(_("退出"), mw)
            quit_action.triggered.connect(mw.close)
            tray_menu.addAction(quit_action)
            mw._tray_icon.setContextMenu(tray_menu)
            mw._tray_icon.show()
        else:
            mw._tray_icon = None

    def on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        """单击/双击托盘图标唤回主窗口。

        ★ 修 §A-37：此前只处理双击，单击托盘图标毫无反应（多数平台的默认预期是
        单击唤回窗口）。单击与双击都给同一反馈，重复触发是无害的幂等操作。
        """
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            mw = self._mw
            mw.show()
            mw.raise_()
            mw.activateWindow()

    def setup_help_center(self) -> None:
        """帮助中心停靠面板（默认收起，由菜单/错误对话框按需展开）。"""
        mw = self._mw
        mw._help_center = HelpCenterDock(mw)
        mw.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, mw._help_center)
        mw._help_center.hide()

    def on_monitor_desktop_notify(self, title: str, message: str) -> None:
        """变更监控触发桌面通知 — 通过系统托盘弹窗。"""
        mw = self._mw
        if mw._tray_icon and mw._tray_icon.isSystemTrayAvailable():
            mw._tray_icon.showMessage(
                title, message, QSystemTrayIcon.MessageIcon.Information, 8000
            )

    def restore_workspace_background(self) -> None:
        """恢复上次选择的工作台背景；媒体不可用时清空设置（不抛异常）。"""
        mw = self._mw
        selected = mw._settings.workspace_background_path
        if not selected:
            return
        try:
            mw._builtin_background().set_media(selected)
        except (OSError, ValueError):
            mw._settings.workspace_background_path = ""
