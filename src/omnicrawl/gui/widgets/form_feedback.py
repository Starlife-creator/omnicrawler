"""Visual form validation feedback utilities.

Provides shake animation and error styling for input widgets,
using ThemeManager tokens exclusively (no hardcoded colors).
"""

from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QSequentialAnimationGroup
from PyQt6.QtWidgets import QWidget


def shake_widget(widget: QWidget, parent: QWidget | None = None) -> None:
    """Create a horizontal shake animation on *widget*.

    The widget oscillates left/right with decreasing amplitude
    (-6, +6, -5, +5, -3, +3, 0) over 350 ms using QEasingCurve.InOutSine.
    """
    base_pos = widget.pos()
    targets = [
        QPoint(base_pos.x() - 6, base_pos.y()),
        QPoint(base_pos.x() + 6, base_pos.y()),
        QPoint(base_pos.x() - 5, base_pos.y()),
        QPoint(base_pos.x() + 5, base_pos.y()),
        QPoint(base_pos.x() - 3, base_pos.y()),
        QPoint(base_pos.x() + 3, base_pos.y()),
        base_pos,
    ]

    group = QSequentialAnimationGroup(parent)
    segment_duration = 350 // len(targets)  # ~50 ms per segment

    for target in targets:
        anim = QPropertyAnimation(widget, b"pos", group)
        anim.setEndValue(target)
        anim.setDuration(segment_duration)
        anim.setEasingCurve(QEasingCurve.Type.InOutSine)
        group.addAnimation(anim)

    group.finished.connect(group.deleteLater)
    group.start()


def set_error_style(widget: QWidget, message: str = "") -> None:
    """Apply a red danger border and optional tooltip to *widget*."""
    widget.setProperty("validation", "error")
    style = widget.style()
    assert style is not None
    style.unpolish(widget)
    style.polish(widget)
    if message:
        widget.setToolTip(message)


def clear_error_style(widget: QWidget) -> None:
    """Restore the default border and clear the tooltip on *widget*."""
    widget.setProperty("validation", "")
    style = widget.style()
    assert style is not None
    style.unpolish(widget)
    style.polish(widget)
    widget.setToolTip("")
