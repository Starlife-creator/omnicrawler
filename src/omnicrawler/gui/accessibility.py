"""Accessible appearance settings shared by every desktop mode."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import QApplication

from .design_system import SCALE_MAX, SCALE_MIN, apply_font_strategy


@dataclass(frozen=True, slots=True)
class AccessibilityProfile:
    scale: int = 100
    high_contrast: bool = False
    color_blind_friendly: bool = False
    reduced_motion: bool = False

    def validated(self) -> AccessibilityProfile:
        return AccessibilityProfile(
            min(SCALE_MAX, max(SCALE_MIN, self.scale)),
            self.high_contrast,
            self.color_blind_friendly,
            self.reduced_motion,
        )


def apply_accessibility(app: QApplication, profile: AccessibilityProfile) -> None:
    """应用无障碍外观档案。

    字体缩放**委托给设计系统**（:func:`apply_font_strategy`）。此前这里用 ``setPointSizeF``
    自己算了一套「基准点值 × 缩放」，而 QSS 用的是 px——两套口径都写 ``app.setFont``，
    且 QSS 的 ``font-size`` 会覆盖 app 字体，于是「界面缩放」改了设置却**看不到效果**
    （§A-23）。现在只有一个实现、一个单位（px），范围常量也只有一处定义。
    """
    selected = profile.validated()
    apply_font_strategy(app, scale=selected.scale)
    # Visual colours are applied by the design system so accessibility variants
    # remain coherent across menus, cards, tables and focus indicators.
    app.setProperty("omnicrawlerHighContrast", selected.high_contrast)
    app.setProperty("omnicrawlerColorBlindFriendly", selected.color_blind_friendly)
    app.setProperty("omnicrawlerReducedMotion", selected.reduced_motion)
