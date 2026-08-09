"""Accessible appearance settings shared by every desktop mode."""

from __future__ import annotations

from dataclasses import dataclass

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication


@dataclass(frozen=True, slots=True)
class AccessibilityProfile:
    scale: int = 100
    high_contrast: bool = False
    color_blind_friendly: bool = False
    reduced_motion: bool = False

    def validated(self) -> AccessibilityProfile:
        return AccessibilityProfile(min(160, max(80, self.scale)), self.high_contrast, self.color_blind_friendly, self.reduced_motion)


def apply_accessibility(app: QApplication, profile: AccessibilityProfile) -> None:
    selected = profile.validated()
    baseline = app.property("omnicrawlBasePointSize")
    if not isinstance(baseline, (int, float)) or baseline <= 0:
        baseline = app.font().pointSizeF()
        app.setProperty("omnicrawlBasePointSize", baseline)
    font = QFont(app.font())
    font.setPointSizeF(max(8.0, float(baseline) * selected.scale / 100))
    app.setFont(font)
    # Visual colours are applied by the design system so accessibility variants
    # remain coherent across menus, cards, tables and focus indicators.
    app.setProperty("omnicrawlHighContrast", selected.high_contrast)
    app.setProperty("omnicrawlColorBlindFriendly", selected.color_blind_friendly)
    app.setProperty("omnicrawlReducedMotion", selected.reduced_motion)
