"""Task execution, stop, progress tracking, and state callbacks."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer, Slot
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from ..core.run_states import is_terminal, normalize_state, state_label
from ..i18n import _
from ..views.login_session_logic import LoginHintGate
from ..widgets.toast import ToastManager
from ._base import _BaseDelegate


class RunController(_BaseDelegate):
    """Task execution, stop, progress tracking, and state callbacks."""

    #: U4-代码：「需要登录」联动提示的去重门（一次运行只提示一次）。
    #: 用类级 ``None`` 默认值 + 首次使用时创建，而不是重写 ``__init__``——
    #: 后者要在类型层引一次 ``MainWindow``，白添一条进环的依赖。
    _login_hint_gate: LoginHintGate | None = None

    def toggle_pause(self) -> None:
        mw = self._mw
        if mw._task_runner.state == "paused":
            mw._task_runner.resume()
            mw._pause_btn.setText(_("Ⅱ 暂停"))
        elif mw._task_runner.state == "running":
            mw._task_runner.pause()
            mw._pause_btn.setText(_("▶ 继续"))

    def run_task(self) -> None:
        mw = self._mw
        # U4-代码：新的一次运行重新开始计数（上次跑提示过，不代表这次不需要登录）。
        # 放在最前：即便环境检查没过，用户"又一次点了运行"这件事本身就该重置。
        if self._login_hint_gate is not None:
            self._login_hint_gate.reset()
        from ..core.validator import plugin_source_kinds, validate_full_config
        if not mw._omnicrawler_available:
            mw._env_checker.check_environment(silent=False)
            if not mw._omnicrawler_available:
                QMessageBox.warning(mw, _("无法运行"), _("omnicrawler 命令不可用，请先配置环境。"))
                return
        errors, warnings = validate_full_config(
            mw._config, extra_source_kinds=plugin_source_kinds(mw._project_root)
        )
        if errors:
            QMessageBox.warning(mw, _("配置校验失败"), "\n".join(errors))
            return
        for w in warnings:
            mw._log_console.append_log(w, "warn")
        if not mw._config_path:
            mw._config_delegate.save_config_as()
            if not mw._config_path:
                return
        mw._run_btn.setEnabled(False)
        mw._stop_btn.setEnabled(True)
        mw._pause_btn.setEnabled(True)
        mw._pause_btn.setText(_("Ⅱ 暂停"))
        mw._progress_bar.setRange(0, 0)
        mw._progress_bar.setValue(0)
        mw._progress_url_label.setText("")
        mw._elapsed_label.setText("00:00:00")
        mw._log_console.clear()
        mw._task_start_time = datetime.now()
        mw._task_elapsed_timer = QTimer(mw)
        mw._task_elapsed_timer.timeout.connect(mw._run_delegate.update_elapsed)
        mw._task_elapsed_timer.start(1000)
        mw._resource_monitor.set_pid(None)
        # ★ S3.1.3 + 竞态修复（2026-09-16 CI 实测）：**先记归属、再启动**。
        # 此前先 `start()` 再赋值，而 `start()` 可能**同步**就走到终态并派发状态回调 ——
        # 回调里的终态分支会把 `_running_task_id` 清成 None，随后这行赋值又把它写回，
        # 于是"结束后应清空"永远不成立（macOS 极快结束的用例上稳定复现）。
        task_id = mw._config.task_id  # 局部 `str`：属性是 `str | None`，这里要的是确定的归属
        mw._running_task_id = task_id
        ok = mw._task_runner.start(mw._config)
        if not ok:
            # 启动失败 ⇒ 本次没有归属，不能把它留给下一次状态回调
            mw._running_task_id = None
        if ok:
            run_config_path = mw._task_runner.config_path or mw._config_path
            mw._task_history.add_record(
                task_id=task_id, project_name=mw._config.project_name,
                config_path=str(run_config_path),
                workspace=str(mw._project_root / mw._config.workspace), status="running")
            mw._resource_monitor.set_pid(mw._task_runner.get_pid())
            mw._set_status(_("运行中"))
        else:
            mw._run_btn.setEnabled(True)
            mw._stop_btn.setEnabled(False)
            mw._pause_btn.setEnabled(False)
            mw._set_status(_("启动失败"))
        mw._stack.setCurrentIndex(2)

    def stop_task(self) -> None:
        mw = self._mw
        mw._task_runner.stop()
        mw._stop_btn.setEnabled(False)
        mw._pause_btn.setEnabled(False)
        mw._set_status(_("正在停止..."))

    def update_elapsed(self) -> None:
        mw = self._mw
        if mw._task_start_time:
            elapsed = datetime.now() - mw._task_start_time
            total_seconds = int(elapsed.total_seconds())
            h, m, s = total_seconds // 3600, (total_seconds % 3600) // 60, total_seconds % 60
            mw._elapsed_label.setText(f"{h:02d}:{m:02d}:{s:02d}")

    @Slot(str, str)
    def on_log_line(self, message: str, level: str) -> None:
        mw = self._mw
        mw._log_console.append_log(message, level)
        # U4-代码：命中 401 / 302→login 时联动提示一次（带"去登录"直达按钮）
        gate = self._login_hint_gate
        if gate is None:
            gate = self._login_hint_gate = LoginHintGate()
        signal = gate.should_announce(message)
        if signal is None:
            return
        ToastManager.instance().show(
            signal.reason,
            kind="warning",
            duration=6000,
            action_text=_("去登录"),
            action_callback=lambda: mw._login_session.open_page(url=signal.login_url),
        )

    @Slot(int, str)
    def on_progress(self, percent: int, url: str) -> None:
        mw = self._mw
        mw._progress_bar.setRange(0, 100)
        mw._progress_bar.setValue(percent)
        mw._progress_url_label.setText(url)

    @Slot(str)
    def on_task_state_changed(self, state: str) -> None:
        mw = self._mw
        mw._status_indicator.state = state
        mw._monitor_status.state = state
        # 文案只在 `gui/core/run_states.py` 定义一次（W6.7）；终态判定用核心词表
        label = state_label(state)
        mw._status_text.setText(label)
        mw._monitor_status_text.setText(label)
        if state == "paused":
            mw._pause_btn.setEnabled(True)
            mw._pause_btn.setText(_("▶ 继续"))
        elif state == "running":
            mw._pause_btn.setEnabled(True)
            mw._pause_btn.setText(_("Ⅱ 暂停"))
        if is_terminal(state):
            mw._run_btn.setEnabled(True)
            mw._stop_btn.setEnabled(False)
            mw._pause_btn.setEnabled(False)
            if mw._task_elapsed_timer:
                mw._task_elapsed_timer.stop()
                mw._task_elapsed_timer = None
            # S3.1.3：结束记录归属启动时的 task_id（运行中切换配置不串历史）
            completed_task_id = mw._running_task_id or mw._config.task_id
            mw._task_history.update_record(completed_task_id, state)
            mw._running_task_id = None
            if normalize_state(state) in {"succeeded", "partial_success", "cancelled"}:
                # 取消也把**已采到的**结果载入结果页（"已有有效输出受保护"）；
                # 下面的自动打开目录/自动导出/提示音只属于正常完成。
                mw._auto_load_results()
            if normalize_state(state) in {"succeeded", "partial_success"}:
                if mw._settings.auto_open_result:
                    mw._open_result_folder()
                if mw._settings.sound_enabled and not mw._dnd_mode:
                    QApplication.beep()
                if mw._settings.markdown_export_enabled:
                    mw._export_markdown()
                if mw._tray_icon and mw._tray_icon.isVisible():
                    icon = QSystemTrayIcon.MessageIcon.Information
                    mw._tray_icon.showMessage(_("OmniCrawler"), _("任务已完成！"), icon, 5000)
            mw._resource_monitor.set_pid(None)
            if mw._task_start_time:
                elapsed = datetime.now() - mw._task_start_time
                mw._finish_label.setText(
                    _("完成: {0}").format(datetime.now().strftime("%H:%M")) + f" ({elapsed.total_seconds():.0f}s)")

    @Slot(str, int)
    def on_task_finished(self, task_id: str, exit_code: int) -> None:
        pass  # state_changed handles everything
