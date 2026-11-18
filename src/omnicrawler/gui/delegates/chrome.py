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

import logging as _chrome_logging
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtCore import Qt as CoreQt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QSlider,
    QSystemTrayIcon,
)

from ..app_icon import app_icon
from ..help_center import HelpCenterDock
from ..i18n import _
from ..widgets.status_indicator import StatusIndicator
from ..widgets.toast import ToastManager
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

    def show_workspace_background_settings(self) -> None:
        mw = self._mw
        controller = mw._builtin_background_controller
        if controller is None or not controller.active:
            ToastManager.instance().info(_("请先选择一个本地工作台背景"))
            return
        dialog = QDialog(mw)
        dialog.setWindowTitle(_("工作台背景显示设置"))
        dialog.setAccessibleName(_("工作台背景显示设置"))
        form = QFormLayout(dialog)

        preset = QComboBox(dialog)
        preset.setAccessibleName(_("视觉预设"))
        for label, value in (
            (_("清晰"), "clear"),
            (_("平衡"), "balanced"),
            (_("专注"), "focus"),
            (_("沉浸"), "immersive"),
            (_("纯背景"), "solid"),
        ):
            preset.addItem(label, value)
        scope = QComboBox(dialog)
        scope.setAccessibleName(_("背景范围"))
        for label, value in (
            (_("整个应用客户区"), "application"),
            (_("完整工作区"), "workspace"),
            (_("当前内容画布"), "canvas"),
        ):
            scope.addItem(label, value)
        scope.setCurrentIndex(max(0, scope.findData(controller.scope)))

        def slider(minimum: int, maximum: int, value: int, name: str) -> QSlider:
            widget = QSlider(CoreQt.Orientation.Horizontal, dialog)
            widget.setAccessibleName(name)
            widget.setRange(minimum, maximum)
            widget.setValue(value)
            return widget

        opacity = slider(5, 100, round(controller.layer.opacity * 100), _("背景可见度"))
        panel = slider(65, 100, controller.panel_opacity, _("前景面板不透明度"))
        dim = slider(0, 85, round(controller.dim * 100), _("暗色遮罩"))
        blur = slider(0, 20, controller.layer.blur, _("静态背景模糊"))
        form.addRow(_("视觉预设"), preset)
        form.addRow(_("背景范围"), scope)
        form.addRow(_("背景可见度"), opacity)
        form.addRow(_("前景面板不透明度"), panel)
        form.addRow(_("暗色遮罩"), dim)
        form.addRow(_("静态背景模糊"), blur)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=dialog)
        form.addRow(buttons)

        def apply_dialog_preset(_index: int) -> None:
            controller.apply_preset(str(preset.currentData()))
            opacity.setValue(round(controller.layer.opacity * 100))
            panel.setValue(controller.panel_opacity)
            dim.setValue(round(controller.dim * 100))
            blur.setValue(controller.layer.blur)

        preset.activated.connect(apply_dialog_preset)
        scope.activated.connect(lambda _index: controller.set_scope(str(scope.currentData())))
        opacity.valueChanged.connect(controller.set_opacity)
        panel.valueChanged.connect(controller.set_panel_opacity)
        dim.valueChanged.connect(controller.set_dim)
        blur.valueChanged.connect(controller.set_blur)
        buttons.rejected.connect(dialog.reject)
        dialog.exec()

    def check_for_updates(self) -> None:
        from ..update_check import _CheckWorker

        mw = self._mw
        if mw._config_path is None:
            ToastManager.instance().info(_("未加载任务配置，无法检查更新"))
            return
        if mw._update_check_worker is not None:
            ToastManager.instance().info(_("更新检查正在进行中"))
            return
        ToastManager.instance().info(_("正在检查更新……"))
        worker = _CheckWorker(str(mw._config_path), mw)
        worker.finished_with.connect(self.on_update_checked)
        mw._update_check_worker = worker
        worker.start()

    def on_update_checked(self, payload: dict[str, Any], _code: int) -> None:
        mw = self._mw
        mw._update_check_worker = None
        status = str(payload.get("status", ""))
        if status == "disabled":
            ToastManager.instance().info(_("应用自更新已禁用（缺少随包信任根）"))
            return
        if status == "ignored":
            ToastManager.instance().info(_("已忽略该版本的更新（命令行 ignore --clear 可恢复）"))
            return
        if status == "failed":
            ToastManager.instance().warning(
                _("检查更新失败：{0}").format(payload.get("detail", ""))
            )
            return
        from ..update_check import (
            available_apply_modes,
            describe_apply_mode,
            format_update_summary,
        )

        summary = format_update_summary(payload)
        if status == "up-to-date":
            ToastManager.instance().info(summary)
            return

        options = payload.get("options") or {}
        modes = available_apply_modes(options)
        if not modes:
            box = QMessageBox(mw)
            box.setWindowTitle(_("检查更新"))
            box.setText(summary)
            box.setAccessibleName(_("检查更新结果"))
            box.exec()
            return

        box = QMessageBox(mw)
        box.setWindowTitle(_("检查更新"))
        box.setText(summary)
        box.setAccessibleName(_("检查更新结果"))
        apply_button = box.addButton(_("立即更新"), QMessageBox.ButtonRole.AcceptRole)
        ignore_button = box.addButton(_("忽略此版本"), QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(_("稍后"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is ignore_button:
            self.ignore_current_update_version()
            return
        if clicked is not apply_button:
            return
        if len(modes) == 1:
            self.start_update_apply(modes[0], describe_apply_mode(modes[0], options))
            return
        chooser = QMessageBox(mw)
        chooser.setWindowTitle(_("选择更新方式"))
        chooser.setAccessibleName(_("选择更新方式"))
        chooser.setText(_("请选择更新方式："))
        buttons: dict[Any, str] = {}
        for mode in modes:
            btn = chooser.addButton(describe_apply_mode(mode, options), QMessageBox.ButtonRole.AcceptRole)
            buttons[btn] = mode
        chooser.addButton(_("取消"), QMessageBox.ButtonRole.RejectRole)
        chooser.exec()
        chosen = buttons.get(chooser.clickedButton())
        if chosen is not None:
            self.start_update_apply(chosen, describe_apply_mode(chosen, options))

    def ignore_current_update_version(self) -> None:
        mw = self._mw
        if mw._config_path is None:
            return
        from ...commands import self_update as cmd_self_update

        try:
            payload, _code = cmd_self_update.ignore(config_path=str(mw._config_path))
        except Exception as exc:  # noqa: BLE001
            _chrome_logging.getLogger(__name__).debug("update ignore failed: %s", exc, exc_info=True)
            ToastManager.instance().warning(_("忽略该版本失败：{0}").format(exc))
            return
        ToastManager.instance().info(str(payload.get("detail") or _("已忽略该版本")))

    def start_update_apply(self, mode: str, description: str) -> None:
        from ..update_check import _ApplyWorker

        mw = self._mw
        confirm = QMessageBox(mw)
        confirm.setWindowTitle(_("确认更新"))
        confirm.setAccessibleName(_("确认更新"))
        confirm.setIcon(QMessageBox.Icon.Warning)
        confirm.setText(
            _("将执行：{0}\n\n这会覆盖应用文件（不会动你的工作区数据）。继续吗？").format(description)
        )
        confirm.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        confirm.setDefaultButton(QMessageBox.StandardButton.No)
        if confirm.exec() != QMessageBox.StandardButton.Yes:
            return
        if mw._update_apply_worker is not None:
            ToastManager.instance().info(_("更新正在进行中"))
            return
        ToastManager.instance().info(_("正在下载并应用更新……"))
        worker = _ApplyWorker(str(mw._config_path), mode, mw)
        worker.finished_with.connect(self.on_update_applied)
        mw._update_apply_worker = worker
        worker.start()

    def on_update_applied(self, payload: dict[str, Any], code: int) -> None:
        from ..update_check import format_apply_result

        mw = self._mw
        mw._update_apply_worker = None
        text = format_apply_result(payload, code)
        if str(payload.get("status", "")) != "applied":
            box = QMessageBox(mw)
            box.setWindowTitle(_("更新未完成"))
            box.setAccessibleName(_("更新未完成"))
            box.setIcon(QMessageBox.Icon.Warning)
            box.setText(text)
            box.exec()
            return
        box = QMessageBox(mw)
        box.setWindowTitle(_("更新完成"))
        box.setAccessibleName(_("更新完成"))
        box.setText(text)
        restart_button = box.addButton(_("立即重启"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(_("稍后手动重启"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is restart_button:
            mw._restart_application()
