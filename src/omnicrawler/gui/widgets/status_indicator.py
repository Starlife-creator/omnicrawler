"""状态指示器组件。

圆形状态指示灯，支持 idle/running/finished/error 四种状态。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

from ..design_system import ThemeManager
from ..i18n import _
from ..motion_signal import MotionSignal


class StatusIndicator(QWidget):
    """圆形状态指示灯。

    颜色含义：
    - idle: 灰色
    - running: 绿色闪烁
    - finished: 蓝色
    - error: 红色
    """

    def __init__(self, parent: QWidget | None = None, size: int = 16) -> None:
        super().__init__(parent)
        self._size = size
        self._state: str = "idle"
        self._blink_on: bool = True
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._toggle_blink)
        self._colors: dict[str, QColor] = {}
        self._refresh_colors()
        self._reduced_motion = False
        MotionSignal.instance().reduced_motion_changed.connect(
            lambda v: setattr(self, "_reduced_motion", v)
        )
        self.setFixedSize(size + 8, size + 8)
        self.setAccessibleName(_("任务状态指示器"))
        self.setToolTip(_("任务状态"))
        ThemeManager.instance().theme_changed.connect(self._refresh_colors)

    @property
    def state(self) -> str:
        return self._state

    @state.setter
    def state(self, value: str) -> None:
        if value != self._state:
            self._state = value
            if value == "running":
                if not self._reduced_motion:
                    self._blink_timer.start(650)
            else:
                self._blink_timer.stop()
                self._blink_on = True
            self.update()
            self._update_tooltip()

    def _toggle_blink(self) -> None:
        self._blink_on = not self._blink_on
        self.update()

    def _refresh_colors(self, *_args) -> None:
        tokens = ThemeManager.instance().tokens
        self._colors = {
            "idle": QColor(tokens.indicator_idle),
            "running": QColor(tokens.indicator_running),
            "finished": QColor(tokens.indicator_finished),
            "error": QColor(tokens.indicator_error),
        }
        self.update()

    def _update_tooltip(self) -> None:
        tips = {
            "idle": _("空闲"),
            "running": _("运行中"),
            "finished": _("已完成"),
            "error": _("错误"),
        }
        self.setToolTip(f"{_('任务状态')}: {tips.get(self._state, self._state)}")
        self.setAccessibleDescription(tips.get(self._state, self._state))

    def paintEvent(self, event: QPaintEvent | None) -> None:
        if event is None:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        color = self._colors.get(self._state, self._colors.get("idle", QColor("#B4B4B4")))
        if self._state == "running" and not self._blink_on:
            lighter = QColor(color)
            lighter.setHslF(lighter.hslHueF(), lighter.hslSaturationF() * 0.4,
                           min(lighter.lightnessF() + 0.35, 1.0))
            color = lighter

        if self._state == "running":
            halo = QColor(color)
            halo.setAlpha(55 if self._blink_on else 20)
            painter.setBrush(halo)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(self.rect().adjusted(1, 1, -1, -1))
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)

        margin = 4
        rect = self.rect().adjusted(margin, margin, -margin, -margin)
        painter.drawEllipse(rect)

        painter.end()
