"""Theme, accessibility, and display scaling delegate."""
from __future__ import annotations

from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication

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
        # S3.1.15：固定行号 2/5/6 改 NavIndex 常量——导航结构调整不再 AssertionError
        _nav2 = mw._nav.item(NavIndex.PDF_WORKBENCH)
        _nav5 = mw._nav.item(NavIndex.RESULTS)
        _nav6 = mw._nav.item(NavIndex.EVIDENCE)
        assert _nav2 is not None
        assert _nav5 is not None
        assert _nav6 is not None
        _nav2.setHidden(mode == "simple")
        _nav5.setHidden(mode == "simple")
        _nav6.setHidden(mode != "developer")
        mw._toggle_btn.setVisible(mode != "simple")
        if hasattr(mw, "_schedule_action"):
            mw._schedule_action.setVisible(mode != "simple")
        if hasattr(mw, "_resource_profile_combo"):
            mw._resource_profile_combo.setVisible(mode != "simple")
        if hasattr(mw, "_resource_label"):
            mw._resource_label.setVisible(mode != "simple")
        if hasattr(mw, "_config_wizard"):
            mw._config_wizard.set_simple_mode(mode == "simple")
        from ...services.ux_service import advanced_rule_summary
        if hasattr(mw, "_advanced_summary"):
            count, sections = advanced_rule_summary(mw._config.passthrough)
            mw._advanced_summary.setVisible(mode == "simple" and count > 0)
            mw._advanced_summary.setText(
                _("已保留 {0} 项高级规则：{1}。切换到专业模式可查看。").format(count, "、".join(sections))
            )
        if mode == "simple" and mw._stack.currentIndex() == 1:
            mw._nav.setCurrentRow(NavIndex.WIZARD)
        messages = {
            "simple": _("简单模式：技术参数已隐藏，使用五步向导即可完成任务"),
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
