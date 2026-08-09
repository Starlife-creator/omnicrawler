"""MotionSignal — unified reduced-motion signal bus.

Replaces per-component QApplication property polling (50ms QTimer in
AmbientHero, per-paintEvent check in StatusIndicator) with a single
singleton QObject that emits ``reduced_motion_changed(bool)`` whenever
the accessibility reduced-motion flag is toggled.

Usage::

    from .motion_signal import MotionSignal

    signal = MotionSignal.instance()
    signal.reduced_motion_changed.connect(self._on_motion_changed)
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal


class _MotionSignal(QObject):
    """Singleton signal bus for reduced-motion state changes.

    Components that need to react to ``reduced_motion`` toggles should
    connect to ``reduced_motion_changed`` instead of polling
    ``QApplication.property("omnicrawlReducedMotion")`` in a timer or
    paint event.
    """

    reduced_motion_changed = pyqtSignal(bool)

    _instance: _MotionSignal | None = None

    def __init__(self) -> None:
        super().__init__()
        self._current: bool = False

    @classmethod
    def instance(cls) -> _MotionSignal:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def is_reduced(self) -> bool:
        """Return the last-known reduced-motion flag value."""
        return self._current

    def notify(self, reduced: bool) -> None:
        """Emit ``reduced_motion_changed`` if the value has changed.

        Called by :class:`delegates.ThemeManager` after the
        accessibility profile is updated.
        """
        if reduced != self._current:
            self._current = reduced
            self.reduced_motion_changed.emit(reduced)


MotionSignal = _MotionSignal
