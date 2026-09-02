"""Theme, accessibility, and display scaling delegate."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from ..accessibility import AccessibilityProfile, apply_accessibility
from ..design_system import apply_design_system
from ..i18n import _
from ..motion_signal import MotionSignal
from ..navigation import NavIndex
from ..widgets.toast import ToastManager
from ._base import _BaseDelegate


class ThemeManager(_BaseDelegate):
    """Manages themes, accessibility, and display scaling."""

    def apply_ui_mode(self, mode: str) -> None:
        mw = self._mw
        if mode not in {"simple", "professional", "developer"}:
            mode = "simple"
        mw._settings.ui_mode = mode
        if hasattr(mw, "_help_center"):
            mw._help_center.set_context(mode, {
                "source_kind": mw._config.source_kind,
                "process_pdf": mw._config.process_pdf,
                "monitor_same_url": mw._config.monitor_same_url,
            })
        if not hasattr(mw, "_nav"):
            return
        # 核心工作流在所有模式都可见；技术工具随用户能力渐进披露。
        simple_hidden = {
            NavIndex.PDF_WORKBENCH,
            NavIndex.CONVERT_TOOL,
            NavIndex.YAML_EDITOR,
            NavIndex.EVIDENCE,
            NavIndex.SCENE,
            NavIndex.CHANGE_MONITOR,
            NavIndex.PLUGIN_MARKET,
            NavIndex.DEVELOPER,
        }
        developer_only = {
            NavIndex.EVIDENCE,
            NavIndex.PLUGIN_MARKET,
            NavIndex.DEVELOPER,
        }
        for index in range(mw._nav.count()):
            item = mw._nav.item(index)
            assert item is not None
            if index == NavIndex.WORK_HEADER:
                hidden = False
            elif index in (NavIndex.AUTOMATION_HEADER, NavIndex.TOOLS_HEADER):
                hidden = mode == "simple"
            elif index == NavIndex.ADVANCED_HEADER:
                hidden = mode == "simple"
            else:
                hidden = index in simple_hidden if mode == "simple" else (
                    index in developer_only if mode == "professional" else False
                )
            item.setHidden(hidden)
        mw._toggle_btn.setVisible(mode != "simple")
        if hasattr(mw, "_schedule_action"):
            mw._schedule_action.setVisible(mode != "simple")
        if hasattr(mw, "_resource_profile_combo"):
            mw._resource_profile_combo.setVisible(mode != "simple")
        if hasattr(mw, "_resource_label"):
            mw._resource_label.setVisible(mode != "simple")
        if hasattr(mw, "_task_canvas"):
            mw._task_canvas.set_simple_mode(mode == "simple")
        from ...services.ux_service import advanced_rule_summary
        if hasattr(mw, "_advanced_summary"):
            count, sections = advanced_rule_summary(mw._config.passthrough)
            mw._advanced_summary.setVisible(mode == "simple" and count > 0)
            mw._advanced_summary.setText(
                _("已保留 {0} 项高级规则：{1}。切换到专业模式可查看。").format(count, "、".join(sections))
            )
        current_item = mw._nav.currentItem()
        if current_item is not None and current_item.isHidden():
            mw._nav.setCurrentRow(NavIndex.WORKSPACE)
        messages = {
            "simple": _("简单模式：仅显示创建、试跑、运行和结果等核心操作"),
            "professional": _("专业模式：可编辑 YAML 和高级采集规则"),
            "developer": _("开发者模式：显示完整配置与诊断工具"),
        }
        ToastManager.instance().info(messages[mode])

    def change_resource_profile(self) -> None:
        mw = self._mw
        profile = str(mw._resource_profile_combo.currentData())
        if profile not in {"economy", "balanced", "performance"}:
            return
        mw._config.resource_profile = profile  # type: ignore[assignment]
        resources = mw._config.passthrough.setdefault("resources", {})
        if isinstance(resources, dict):
            resources["profile"] = profile
        descriptions = {
            "economy": _("省电模式：最多 2 个并发、1 个浏览器实例"),
            "balanced": _("均衡模式：笔记本日常推荐"),
            "performance": _("全速模式：建议插电并保持散热"),
        }
        ToastManager.instance().info(descriptions[profile])

    def refresh_accessibility(self) -> None:
        mw = self._mw
        app = QApplication.instance()
        if app is None:
            return
        assert isinstance(app, QApplication)
        apply_accessibility(app, AccessibilityProfile(
            mw._settings.interface_scale,
            mw._settings.high_contrast,
            mw._settings.color_blind_friendly,
            mw._settings.reduced_motion,
        ))
        MotionSignal.instance().notify(mw._settings.reduced_motion)
        self.apply_visual_theme()
        if hasattr(mw, "_page_transition"):
            mw._page_transition.reduced_motion = mw._settings.reduced_motion

    def apply_visual_theme(self) -> None:
        mw = self._mw
        app = QApplication.instance()
        if app is None:
            return
        assert isinstance(app, QApplication)
        theme = mw._settings.theme
        if theme == "system":
            theme = "dark" if app.palette().color(QPalette.ColorRole.Window).lightness() < 128 else "light"
        mw._visual_tokens = apply_design_system(
            app, theme,
            high_contrast=mw._settings.high_contrast,
            color_blind_friendly=mw._settings.color_blind_friendly,
        )
        background = getattr(mw, "_active_plugin_background", None)
        if background is not None and background.active:
            background._apply_host_surface_theme(True)
            minimized = bool(
                mw.windowState() & Qt.WindowState.WindowMinimized
            ) or not mw.isVisible()
            background._set_host_paused(mw._settings.reduced_motion or minimized)

    def set_interface_scale(self, value: int) -> None:
        mw = self._mw
        mw._settings.interface_scale = value
        self.refresh_accessibility()
        mw._set_status(_("界面缩放已应用；部分控件将在重新打开窗口后达到最佳布局"))

    def set_accessibility_option(self, name: str, value: bool) -> None:
        mw = self._mw
        setattr(mw._settings, name, value)
        self.refresh_accessibility()

    def set_theme(self, theme: str) -> None:
        mw = self._mw
        mw._settings.theme = theme
        self.apply_visual_theme()

    def toggle_dnd(self, enabled: bool) -> None:
        mw = self._mw
        mw._dnd_mode = enabled
        mw._settings.dnd_enabled = enabled
        self.update_dnd_label()

    def update_dnd_label(self) -> None:
        mw = self._mw
        if mw._dnd_mode:
            mw._dnd_label.setText("🌙 DND")
            mw._dnd_label.setVisible(True)
        else:
            mw._dnd_label.setText("☀️")
            mw._dnd_label.setVisible(False)
