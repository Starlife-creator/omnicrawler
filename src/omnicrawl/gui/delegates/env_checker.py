"""Environment detection, first-launch guidance, and quick experience."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtWidgets import QCheckBox, QFileDialog, QInputDialog, QMessageBox

from ..i18n import _
from ..widgets.toast import ToastManager
from ._base import _BaseDelegate


class EnvironmentChecker(_BaseDelegate):
    """Environment detection, first-launch guidance, and quick experience."""

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
                configure_data_mode("custom", directory) if directory else configure_data_mode("portable")
            else:
                configure_data_mode("portable")
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
        mw._nav.setCurrentRow(0)
        mw._config_wizard.restart()
        mw._config_wizard.step1_page.focus_primary_url()
        msg = QMessageBox(mw)
        msg.setWindowTitle(_("欢迎使用 OmniCrawler"))
        msg.setIcon(QMessageBox.Icon.Information)
        msg.setText(_("OmniCrawler 可以从网站提取结构化数据。\n\n"
            "  · 静态网页：使用向导输入网址即可\n"
            "  · 动态页面：浏览器模式自动渲染\n"
            "  · 数据接口：直接调用 API 并认证\n"
            "  · PDF 文档：自动下载并 OCR 识别\n\n"
            "选择开始使用的方式："))
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
        from ...core.runtime_paths import resolve_cli_command
        from ..runner.env_checker import check_omnicrawl, try_auto_install
        msg = QMessageBox(mw)
        msg.setWindowTitle(_("环境配置"))
        msg.setText(_("未检测到 omnicrawl 命令。请选择配置方式："))
        auto_btn = msg.addButton(_("自动安装"), QMessageBox.ButtonRole.ActionRole)
        manual_btn = msg.addButton(_("手动指定路径"), QMessageBox.ButtonRole.ActionRole)
        msg.addButton(_("跳过（仅编辑配置）"), QMessageBox.ButtonRole.RejectRole)
        msg.exec()
        clicked = msg.clickedButton()
        if clicked == auto_btn:
            ok, result = try_auto_install(mw._project_root)
            if ok:
                mw._omnicrawl_available = True
                mw._omnicrawl_path = resolve_cli_command("omnicrawl")
                mw._settings.omnicrawl_path = mw._omnicrawl_path
                mw._task_runner.set_omnicrawl_path(mw._omnicrawl_path)
                ToastManager.instance().success(_("安装成功！"))
            else:
                QMessageBox.warning(mw, _("安装失败"), result)
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
