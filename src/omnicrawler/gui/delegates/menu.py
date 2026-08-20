"""Menu bar construction delegate."""
from __future__ import annotations

from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import QMenu

from ..i18n import _
from ._base import _BaseDelegate


class MenuBuilder(_BaseDelegate):
    """Builds and manages the main window menu bar."""

    def setup(self) -> None:
        """Set up the menu bar with all menus and actions."""
        mw = self._mw
        menubar = mw.menuBar()
        assert menubar is not None

        # File menu
        file_menu = menubar.addMenu(_("文件(&F)"))
        assert file_menu is not None
        new_action = QAction(_("新建配置(&N)"), mw)
        new_action.triggered.connect(mw._new_config)
        file_menu.addAction(new_action)

        open_action = QAction(_("打开配置(&O)..."), mw)
        open_action.setShortcut(QKeySequence.StandardKey.Open)
        open_action.triggered.connect(mw._open_config)
        file_menu.addAction(open_action)

        file_menu.addSeparator()
        mw._recent_menu = QMenu(_("最近文件"), mw)
        file_menu.addMenu(mw._recent_menu)
        mw._refresh_recent_menu()

        file_menu.addSeparator()
        save_action = QAction(_("保存(&S)"), mw)
        save_action.setShortcut(QKeySequence(mw._settings.shortcuts["save"]))
        save_action.triggered.connect(mw._save_config)
        file_menu.addAction(save_action)

        save_as_action = QAction(_("另存为..."), mw)
        save_as_action.triggered.connect(mw._save_config_as)
        file_menu.addAction(save_as_action)

        history_action = QAction(_("配置历史与恢复..."), mw)
        history_action.triggered.connect(mw._show_config_history)
        file_menu.addAction(history_action)

        file_menu.addSeparator()
        import_action = QAction(_("导入配置包..."), mw)
        import_action.triggered.connect(mw._import_config_package)
        file_menu.addAction(import_action)

        export_action = QAction(_("导出配置包..."), mw)
        export_action.triggered.connect(mw._export_config_package)
        file_menu.addAction(export_action)

        file_menu.addSeparator()
        quit_action = QAction(_("退出(&Q)"), mw)
        quit_action.setShortcut(QKeySequence.StandardKey.Quit)
        quit_action.triggered.connect(mw.close)
        file_menu.addAction(quit_action)

        # Settings menu
        settings_menu = menubar.addMenu(_("设置(&S)"))
        assert settings_menu is not None
        recheck_action = QAction(_("重新检测环境"), mw)
        recheck_action.triggered.connect(mw._recheck_env)
        settings_menu.addAction(recheck_action)

        switch_project_action = QAction(_("切换项目目录..."), mw)
        switch_project_action.triggered.connect(mw._switch_project)
        settings_menu.addAction(switch_project_action)

        mw._schedule_action = QAction(_("定时任务..."), mw)
        mw._schedule_action.triggered.connect(mw._manage_schedules)
        settings_menu.addAction(mw._schedule_action)

        preflight_action = QAction(_("运行前检查与小样本试跑..."), mw)
        preflight_action.triggered.connect(mw._show_preflight)
        settings_menu.addAction(preflight_action)

        error_center_action = QAction(_("打开统一错误中心"), mw)
        error_center_action.triggered.connect(mw._open_error_center)
        settings_menu.addAction(error_center_action)

        compare_runs_action = QAction(_("对比两次运行..."), mw)
        compare_runs_action.triggered.connect(mw._show_run_comparison)
        settings_menu.addAction(compare_runs_action)

        plugin_action = QAction(_("插件管理与权限..."), mw)
        plugin_action.triggered.connect(mw._manage_plugins)
        settings_menu.addAction(plugin_action)

        pdf_region_action = QAction(_("PDF 页面框选字段..."), mw)
        pdf_region_action.triggered.connect(lambda: mw._show_pdf_region_dialog())
        settings_menu.addAction(pdf_region_action)

        recorder_action = QAction(_("录制网页操作..."), mw)
        recorder_action.setToolTip(_("打开浏览器，正常点击和输入；关闭窗口后自动写入当前配置"))
        recorder_action.triggered.connect(mw._record_browser_actions)
        settings_menu.addAction(recorder_action)

        settings_menu.addSeparator()
        ai_center_action = QAction(_("AI 服务中心..."), mw)
        ai_center_action.triggered.connect(mw._open_ai_service_center)
        settings_menu.addAction(ai_center_action)

        # Theme submenu
        theme_menu = QMenu(_("主题"), mw)
        for theme_name, theme_id in [(_("明亮"), "light"), (_("黑暗"), "dark"), (_("跟随系统"), "system")]:
            theme_action = QAction(theme_name, mw)
            theme_action.setData(theme_id)
            theme_action.triggered.connect(lambda checked, t=theme_id: mw._set_theme(t))
            theme_menu.addAction(theme_action)
        try:
            from ..design_system import plugin_theme_labels

            for label, theme_id in plugin_theme_labels():
                plugin_theme_action = QAction(label, mw)
                plugin_theme_action.setData(theme_id)
                plugin_theme_action.triggered.connect(
                    lambda checked, t=theme_id: mw._set_theme(t)
                )
                theme_menu.addAction(plugin_theme_action)
        except Exception:  # noqa: BLE001 - 插件主题缺失不影响内置主题
            pass
        settings_menu.addMenu(theme_menu)

        # Accessibility submenu
        accessibility_menu = QMenu(_("无障碍与显示"), mw)
        for label, scale in ((_('紧凑 90%'), 90), (_('标准 100%'), 100), (_('大字体 125%'), 125), (_('特大字体 150%'), 150)):
            action = QAction(label, mw)
            action.triggered.connect(lambda _checked=False, value=scale: mw._set_interface_scale(value))
            accessibility_menu.addAction(action)
        accessibility_menu.addSeparator()
        contrast_action = QAction(_("高对比度"), mw)
        contrast_action.setCheckable(True)
        contrast_action.setChecked(mw._settings.high_contrast)
        contrast_action.toggled.connect(lambda value: mw._set_accessibility_option("high_contrast", value))
        accessibility_menu.addAction(contrast_action)
        color_action = QAction(_("色盲友好配色"), mw)
        color_action.setCheckable(True)
        color_action.setChecked(mw._settings.color_blind_friendly)
        color_action.toggled.connect(lambda value: mw._set_accessibility_option("color_blind_friendly", value))
        accessibility_menu.addAction(color_action)
        motion_action = QAction(_("减少动画"), mw)
        motion_action.setCheckable(True)
        motion_action.setChecked(mw._settings.reduced_motion)
        motion_action.toggled.connect(lambda value: mw._set_accessibility_option("reduced_motion", value))
        accessibility_menu.addAction(motion_action)
        settings_menu.addMenu(accessibility_menu)

        settings_menu.addSeparator()
        auto_open_action = QAction(_("任务完成后自动打开结果文件夹"), mw)
        auto_open_action.setCheckable(True)
        auto_open_action.setChecked(mw._settings.auto_open_result)
        auto_open_action.toggled.connect(lambda v: setattr(mw._settings, 'auto_open_result', v))
        settings_menu.addAction(auto_open_action)

        sound_action = QAction(_("任务完成声音提示"), mw)
        sound_action.setCheckable(True)
        sound_action.setChecked(mw._settings.sound_enabled)
        sound_action.toggled.connect(lambda v: setattr(mw._settings, 'sound_enabled', v))
        settings_menu.addAction(sound_action)

        dnd_action = QAction(_("请勿打扰模式"), mw)
        dnd_action.setCheckable(True)
        dnd_action.setChecked(mw._dnd_mode)
        dnd_action.setShortcut(QKeySequence(mw._settings.shortcuts["toggle_dnd"]))
        dnd_action.toggled.connect(mw._toggle_dnd)
        settings_menu.addAction(dnd_action)

        settings_menu.addSeparator()
        export_md_action = QAction(_("任务完成后自动导出 Markdown"), mw)
        export_md_action.setCheckable(True)
        export_md_action.setChecked(mw._settings.markdown_export_enabled)
        export_md_action.toggled.connect(lambda v: setattr(mw._settings, 'markdown_export_enabled', v))
        settings_menu.addAction(export_md_action)

        settings_menu.addSeparator()
        stealth_action = QAction(_("反检测与隐身设置..."), mw)
        stealth_action.triggered.connect(mw._show_stealth_settings)
        settings_menu.addAction(stealth_action)

        settings_menu.addSeparator()
        shortcuts_action = QAction(_("快捷键说明"), mw)
        shortcuts_action.triggered.connect(mw._show_shortcuts)
        settings_menu.addAction(shortcuts_action)

        capabilities_action = QAction(_("运行能力与自包含组件..."), mw)
        capabilities_action.triggered.connect(mw._show_capabilities)
        settings_menu.addAction(capabilities_action)

        about_action = QAction(_("关于"), mw)
        about_action.triggered.connect(mw._show_about)
        settings_menu.addAction(about_action)

        # Help menu
        help_menu = menubar.addMenu(_("帮助(&H)"))
        assert help_menu is not None
        selector_help_action = QAction(_("选择器语法帮助"), mw)
        selector_help_action.triggered.connect(mw._show_selector_help)
        help_menu.addAction(selector_help_action)
        quick_start_action = QAction(_("快速上手指南"), mw)
        quick_start_action.triggered.connect(mw._show_quick_start)
        help_menu.addAction(quick_start_action)
        faq_action = QAction(_("常见问题"), mw)
        faq_action.triggered.connect(mw._show_faq)
        help_menu.addAction(faq_action)
        help_center_action = QAction(_("搜索帮助中心"), mw)
        help_center_action.setShortcut(QKeySequence.StandardKey.HelpContents)
        help_center_action.triggered.connect(lambda: mw._help_center.focus_search())
        help_menu.addAction(help_center_action)
