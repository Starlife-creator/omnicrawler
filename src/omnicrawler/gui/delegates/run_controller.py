"""Task execution, stop, progress tracking, and state callbacks."""
from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QTimer, Slot
from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon

from ..i18n import _
from ._base import _BaseDelegate


class RunController(_BaseDelegate):
    """Task execution, stop, progress tracking, and state callbacks."""

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
            mw._save_config_as()
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
        mw._task_elapsed_timer.timeout.connect(mw._update_elapsed)
        mw._task_elapsed_timer.start(1000)
        mw._resource_monitor.set_pid(None)
        ok = mw._task_runner.start(mw._config)
        if ok:
            # S3.1.3：记录本次运行归属的 task_id——结束时用它而非当前配置
            mw._running_task_id = mw._config.task_id
            run_config_path = mw._task_runner.config_path or mw._config_path
            mw._task_history.add_record(
                task_id=mw._running_task_id, project_name=mw._config.project_name,
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
        self._mw._log_console.append_log(message, level)

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
        state_text = {"idle": _("空闲"), "running": _("运行中"), "paused": _("已暂停"),
                      "stopping": _("正在安全停止"), "finished": _("已完成"), "error": _("错误")}
        mw._status_text.setText(state_text.get(state, state))
        mw._monitor_status_text.setText(state_text.get(state, state))
        if state == "paused":
            mw._pause_btn.setEnabled(True)
            mw._pause_btn.setText(_("▶ 继续"))
        elif state == "running":
            mw._pause_btn.setEnabled(True)
            mw._pause_btn.setText(_("Ⅱ 暂停"))
        if state in ("finished", "error"):
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
            if state == "finished":
                mw._auto_load_results()
                if mw._settings.auto_open_result:
                    mw._open_result_folder()
                if mw._settings.sound_enabled and not mw._dnd_mode:
                    QApplication.beep()
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
