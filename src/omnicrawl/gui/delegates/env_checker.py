"""Environment detection, first-launch guidance, and quick experience."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QCheckBox, QFileDialog, QInputDialog, QMessageBox

from ..i18n import _
from ..navigation import NavIndex
from ..widgets.toast import ToastManager
from ._base import _BaseDelegate


class EnvironmentChecker(_BaseDelegate):
    """Environment detection, first-launch guidance, and quick experience."""

    # ── S3.1.1：后台任务结果应用（主线程回调）────────────────────────

    def _apply_probe_result(self, available: bool, version: str) -> None:
        mw = self._mw
        mw._omnicrawl_available = available
        if available:
            mw._settings.omnicrawl_version = version
            mw._settings.env_checked = True
            ToastManager.instance().success(_("环境就绪: {0}").format(version))
        else:
            QMessageBox.warning(
                mw, _("环境检测"),
                _("仍无法启动内置引擎，请重新解压完整便携包或检查安全软件拦截。"),
            )

    def _apply_install_result(self, ok: bool, result: str) -> None:
        mw = self._mw
        from ...core.runtime_paths import resolve_cli_command

        if ok:
            mw._omnicrawl_available = True
            mw._omnicrawl_path = resolve_cli_command("omnicrawl")
            mw._settings.omnicrawl_path = mw._omnicrawl_path
            mw._task_runner.set_omnicrawl_path(mw._omnicrawl_path)
            ToastManager.instance().success(_("安装成功！"))
        else:
            QMessageBox.warning(mw, _("安装失败"), result)

    def ensure_data_mode_choice(self) -> None:
        mw = self._mw
        from ...core.runtime_paths import application_dir, configure_data_mode, is_frozen
        if not is_frozen():
            return
        app_root = application_dir()
        if any((app_root / name).is_file() for name in ("PORTABLE.flag", "portable.flag", "data-mode.json")):
            return
        choices = [_('完全便携：数据放在程序目录'), _('本机数据：程序便携，数据放在本机'), _('自选数据目录')]
        selected, accepted = QInputDialog.getItem(
            mw, _("首次启动：选择数据位置"),
            _("以后可在设置中修改；迁移时可复制整个工作区。"), choices, 0, False,
        )
        if not accepted:
            selected = choices[0]
        try:
            if selected == choices[1]:
                configure_data_mode("local")
            elif selected == choices[2]:
                directory = QFileDialog.getExistingDirectory(mw, _("选择工作区根目录"), str(app_root))
                if not directory:
                    # S3.1.11：取消选择目录后不静默回退 portable——
                    # 保留当前数据模式并提示，用户明确选择不被静默丢弃
                    ToastManager.instance().warning(_("已取消自定义数据目录选择；数据位置保持不变。"))
                    return
                configure_data_mode("custom", directory)
            else:
                configure_data_mode("portable")
            # F53：数据模式变更会改变 portable_data_root() 结果；
            # 重置设置单例，让 settings.ini 落在新数据根，避免本会话配置写错目录。
            from ..settings import AppSettings

            AppSettings.reset()
            AppSettings.instance()
        except OSError as exc:
            QMessageBox.warning(mw, _("数据位置未保存"), str(exc))

    def check_environment(self, silent: bool = True) -> bool:
        mw = self._mw
        from ...core.runtime_paths import bundled_browser_available
        from ..runner.env_checker import check_omnicrawl
        available, version = check_omnicrawl(mw._omnicrawl_path)
        mw._omnicrawl_available = available
        if available:
            mw._settings.omnicrawl_version = version
            mw._settings.env_checked = True
            if not silent:
                browser_status = _("，内置 Chromium 已就绪") if bundled_browser_available() else ""
                ToastManager.instance().info(_("环境就绪: {0}{1}").format(version, browser_status))
            return True
        return False

    def recheck_env(self) -> None:
        mw = self._mw
        self.check_environment(silent=False)
        if not mw._omnicrawl_available:
            QMessageBox.warning(mw, _("环境检测"), _("omnicrawl 命令不可用。请确保 OmniCrawler 框架已安装。"))

    def switch_project(self) -> None:
        mw = self._mw
        directory = QFileDialog.getExistingDirectory(mw, _("选择 OmniCrawler 项目根目录"), str(mw._project_root))
        if directory:
            mw._project_root = Path(directory)
            mw._settings.project_root = str(mw._project_root)
            # S3.1.27：重建依赖项目根的组件（task_runner/autosave/history/template_loader），
            # 不再只改标签
            mw._rebuild_project_components()
            mw._update_project_label()
            ToastManager.instance().info(_("项目目录已切换"))

    def update_project_label(self) -> None:
        mw = self._mw
        path_str = str(mw._project_root)
        if len(path_str) > 60:
            path_str = "..." + path_str[-57:]
        mw._project_label.setText(_("📁 ") + path_str)

    def on_first_launch(self) -> None:
        mw = self._mw
        self.check_environment(silent=True)
        if not mw._omnicrawl_available:
            self.show_env_setup_dialog()
        mw._autosave.scan_and_emit()
        if mw._settings.is_first_launch and not mw._settings.has_run_history:
            mw._settings.is_first_launch = False
            self.show_welcome_dialog()

    def show_welcome_dialog(self) -> None:
        mw = self._mw
        mw._nav.setCurrentRow(NavIndex.HOME)
        mw._task_canvas.restart()
        mw._task_canvas.focus_url_input()
        msg = QMessageBox(mw)
        msg.setWindowTitle(_("欢迎使用 OmniCrawler"))
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(_("OmniCrawler 可以从网站提取结构化数据。\n\n")
            + _("  · 静态网页：使用向导输入网址即可\n")
            + _("  · 动态页面：浏览器模式自动渲染\n")
            + _("  · 数据接口：直接调用 API 并认证\n")
            + _("  · PDF 文档：自动下载并 OCR 识别\n\n")
            + _("选择开始使用的方式："))
        wizard_btn = msg.addButton(_("1. 向导模式"), QMessageBox.ButtonRole.AcceptRole)
        demo_btn = msg.addButton(_("2. 5 分钟演示"), QMessageBox.ButtonRole.ActionRole)
        tmpl_btn = msg.addButton(_("3. 浏览模板"), QMessageBox.ButtonRole.ActionRole)
        msg.setDefaultButton(wizard_btn)
        cb = QCheckBox(_("不再显示"))
        cb.setChecked(False)
        msg.setCheckBox(cb)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked == demo_btn:
            mw._start_demo()
        elif clicked == tmpl_btn:
            mw._open_template_browser()

    def show_env_setup_dialog(self) -> None:
        mw = self._mw
        from ...core.runtime_paths import bundled_cli_path, is_frozen
        from ..runner.env_checker import check_omnicrawl, try_auto_install

        if is_frozen():
            # F33/F34：便携版没有 pip，也不该引导自动安装；
            # 未就绪多为冷启动慢或被杀软拦截，主按钮改为"重试检测"
            bundled = bundled_cli_path()
            path_hint = str(bundled) if bundled else _("（未找到内置 CLI）")
            msg = QMessageBox(mw)
            msg.setWindowTitle(_("环境配置"))
            msg.setText(_("内置引擎暂未就绪。\n已探测到: {0}\n\n"
                          + _("可能原因：首次冷启动较慢或被杀毒软件拦截。\n")
                          + _("请点击「重试检测」；若持续失败请重新解压完整便携包。")).format(path_hint))
            retry_btn = msg.addButton(_("重试检测"), QMessageBox.ButtonRole.AcceptRole)
            # 便携包自包含：不提供"手动指定路径"/"自动安装"（外部 CLI 无配套 worker 且无 pip）
            msg.addButton(_("跳过（仅编辑配置）"), QMessageBox.ButtonRole.RejectRole)
            msg.exec()
            clicked = msg.clickedButton()
            if clicked == retry_btn:
                # S3.1.1：探测移入后台线程，界面不再冻结（冷启动可能 60s）
                from ..core.background_worker import BackgroundWorker, run_worker

                class _ProbeWorker(BackgroundWorker):
                    def work(self) -> tuple[bool, str]:
                        return check_omnicrawl(mw._omnicrawl_path)

                run_worker(
                    _ProbeWorker(),
                    on_succeeded=lambda payload: self._apply_probe_result(*payload),
                )
            return

        msg = QMessageBox(mw)
        msg.setWindowTitle(_("环境配置"))
        msg.setText(_("未检测到 omnicrawl 命令。请选择配置方式："))
        auto_btn = msg.addButton(_("自动安装"), QMessageBox.ButtonRole.ActionRole)
        manual_btn = msg.addButton(_("手动指定路径"), QMessageBox.ButtonRole.ActionRole)
        msg.addButton(_("跳过（仅编辑配置）"), QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked == auto_btn:
            # S3.1.1：pip 安装移入后台线程，安装期间界面可交互
            from ..core.background_worker import BackgroundWorker, run_worker

            class _InstallWorker(BackgroundWorker):
                def work(self) -> tuple[bool, str]:
                    return try_auto_install(mw._project_root)

            run_worker(
                _InstallWorker(),
                on_succeeded=lambda payload: self._apply_install_result(*payload),
            )
        elif clicked == manual_btn:
            filepath, _filter = QFileDialog.getOpenFileName(mw, _("选择 omnicrawl 可执行文件"),
                filter=_("可执行文件 (*.exe *.bat *.cmd);;所有文件 (*)"))
            if filepath:
                available, version = check_omnicrawl(filepath)
                if available:
                    mw._omnicrawl_available = True
                    mw._omnicrawl_path = filepath
                    mw._settings.omnicrawl_path = filepath
                    mw._settings.omnicrawl_version = version
                    mw._task_runner.set_omnicrawl_path(filepath)
                    ToastManager.instance().success(_("环境就绪"))
                else:
                    QMessageBox.warning(mw, _("无效路径"), _("指定的路径无效"))

    def quick_experience(self) -> None:
        mw = self._mw
        if mw._config.fields or mw._config.seed_urls:
            reply = QMessageBox.question(mw, _("快速体验"), _("当前配置将被覆盖，是否继续？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if reply != QMessageBox.StandardButton.Yes:
                return
        config = mw._template_loader.load_template("news")
        if config is None:
            QMessageBox.warning(mw, _("模板加载失败"), _("无法加载 news 模板"))
            return
        config.seed_urls = ["https://www.example.com/news"]
        for f in config.fields:
            if "{{title_selector}}" in f.selector:
                f.selector = "h2 a"
            elif "{{link_selector}}" in f.selector:
                f.selector = "h2 a"
            elif "{{date_selector}}" in f.selector:
                f.selector = ".date"
        mw._config = config
        mw._config_path = None
        mw._config_label.setText(_("快速体验 - 未保存"))
        mw._rebuild_wizard()
        ToastManager.instance().info(_("示例配置已加载，点击 ▶ 运行即可体验"))
        reply = QMessageBox.question(mw, _("快速体验"), _("示例配置已加载！是否立即运行此任务？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            mw._run_task()
