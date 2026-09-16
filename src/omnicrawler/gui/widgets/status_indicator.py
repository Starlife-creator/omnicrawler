"""状态指示器组件。

圆形状态指示灯，支持 idle/running/finished/error 四种状态。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QWidget

from ..core.run_states import state_color_token, state_label
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

    def _refresh_colors(self, *_args: object) -> None:
        tokens = ThemeManager.instance().tokens
        # 状态 → 颜色令牌只在 `gui/core/run_states.py` 定义一次（W6.7）。
        # 取消/暂停/部分成功现在都有**专属令牌**（此前只能借用 idle/error，
        # 见本文件旧注释里那句"要专属色就新增令牌"）。
        self._colors = {
            name: QColor(getattr(tokens, state_color_token(name)))
            for name in ("idle", "running", "paused", "stopping", "succeeded",
                         "partial_success", "failed", "cancelled")
        }
        self.update()

    def _update_tooltip(self) -> None:
        label = state_label(self._state)
        self.setToolTip(f"{_('任务状态')}: {label}")
        self.setAccessibleDescription(label)

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
