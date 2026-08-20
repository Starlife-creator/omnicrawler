"""Toolbar construction delegate."""
from __future__ import annotations

from PySide6.QtWidgets import QComboBox, QLabel, QPushButton

from ..i18n import _
from ._base import _BaseDelegate


class ToolbarManager(_BaseDelegate):
    """Builds and manages the main toolbar."""

    def setup(self) -> None:
        """Set up the main toolbar with all buttons."""
        mw = self._mw
        toolbar = mw.addToolBar(_("主工具栏"))
        assert toolbar is not None
        toolbar.setObjectName("mainToolbar")
        toolbar.setMovable(False)
        shortcuts = mw._settings.shortcuts

        save_btn = QPushButton(_("💾 保存"))
        save_btn.clicked.connect(mw._save_config)
        save_btn.setToolTip(_("保存配置 ({0})").format(shortcuts["save"]))
        toolbar.addWidget(save_btn)
        toolbar.addSeparator()

        mw._run_btn = QPushButton(_("▶ 运行"))
        mw._run_btn.clicked.connect(mw._run_task)
        mw._run_btn.setProperty("success", True)
        mw._run_btn.setToolTip(_("运行任务 ({0})").format(shortcuts["run"]))
        toolbar.addWidget(mw._run_btn)

        mw._stop_btn = QPushButton(_("■ 停止"))
        mw._stop_btn.clicked.connect(mw._stop_task)
        mw._stop_btn.setEnabled(False)
        mw._stop_btn.setToolTip(_("停止任务 ({0})").format(shortcuts["stop"]))
        toolbar.addWidget(mw._stop_btn)

        mw._pause_btn = QPushButton(_("Ⅱ 暂停"))
        mw._pause_btn.clicked.connect(mw._toggle_pause)
        mw._pause_btn.setEnabled(False)
        toolbar.addWidget(mw._pause_btn)
        toolbar.addSeparator()

        mw._toggle_btn = QPushButton(_("⇄ 编辑器"))
        mw._toggle_btn.clicked.connect(mw._toggle_wizard_editor)
        mw._toggle_btn.setToolTip(_("切换向导/编辑器 ({0})").format(shortcuts["toggle_editor"]))
        toolbar.addWidget(mw._toggle_btn)
        toolbar.addSeparator()

        quick_btn = QPushButton(_("🚀 快速体验"))
        quick_btn.clicked.connect(mw._quick_experience)
        quick_btn.setToolTip(_("一键加载示例配置并运行演示任务"))
        toolbar.addWidget(quick_btn)
        toolbar.addSeparator()

        template_btn = QPushButton(_("🗂 模板"))
        template_btn.clicked.connect(mw._show_template_library)
        template_btn.setToolTip(_("打开模板库 ({0})").format(shortcuts["open_templates"]))
        toolbar.addWidget(template_btn)

        preflight_btn = QPushButton(_("✓ 试跑检查"))
        preflight_btn.clicked.connect(mw._show_preflight)
        preflight_btn.setToolTip(_("检查依赖、磁盘与配置，并可独立试跑 3 页"))
        toolbar.addWidget(preflight_btn)
        toolbar.addSeparator()

        mw._resource_label = QLabel(_("资源:"))
        toolbar.addWidget(mw._resource_label)
        mw._resource_profile_combo = QComboBox()
        mw._resource_profile_combo.addItem(_("省电"), "economy")
        mw._resource_profile_combo.addItem(_("均衡"), "balanced")
        mw._resource_profile_combo.addItem(_("全速"), "performance")
        for index in range(mw._resource_profile_combo.count()):
            if mw._resource_profile_combo.itemData(index) == mw._config.resource_profile:
                mw._resource_profile_combo.setCurrentIndex(index)
                break
        mw._resource_profile_combo.currentIndexChanged.connect(mw._change_resource_profile)
        mw._resource_profile_combo.setToolTip(_("省电适合电池；均衡适合日常；全速建议插电使用"))
        toolbar.addWidget(mw._resource_profile_combo)
        toolbar.addSeparator()

        toolbar.addWidget(QLabel(_("界面:")))
        mw._mode_combo = QComboBox()
        mw._mode_combo.addItem(_("简单模式"), "simple")
        mw._mode_combo.addItem(_("专业模式"), "professional")
        mw._mode_combo.addItem(_("开发者模式"), "developer")
        for index in range(mw._mode_combo.count()):
            if mw._mode_combo.itemData(index) == mw._settings.ui_mode:
                mw._mode_combo.setCurrentIndex(index)
                break
        mw._mode_combo.currentIndexChanged.connect(
            lambda: mw._apply_ui_mode(str(mw._mode_combo.currentData()))
        )
        mw._mode_combo.setToolTip(_("简单模式隐藏技术配置；随时可以切换，项目内容不会改变"))
        toolbar.addWidget(mw._mode_combo)
