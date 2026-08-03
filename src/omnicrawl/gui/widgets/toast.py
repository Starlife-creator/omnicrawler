"""Toast / Snackbar 全局通知组件。

非侵入式浮动通知，替代阻塞式 QMessageBox 和易被忽略的 QStatusBar。
支持自动消失、堆叠显示、多种类型和自定义操作按钮。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable

from PyQt6.QtCore import (
    QPropertyAnimation,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..design_system import ThemeManager, rgba_token_to_qcolor
from ..icon_registry import IconRegistry


class Toast(QFrame):
    """单条 Toast 通知卡片。

    特性：
    - 自动消失（带倒计时进度条）
    - 手动关闭按钮
    - 滑入/滑出动画
    - 支持操作按钮回调
    """

    closed = pyqtSignal()

    def __init__(
        self,
        message: str,
        *,
        kind: str = "info",
        duration: int = 3000,
        action_text: str = "",
        action_callback: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("toast")
        self.setProperty("toastKind", kind)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._duration = duration
        self._action_callback = action_callback
        self._closing = False
        # QPropertyAnimation takes its target as the first argument, not its
        # parent.  Keep explicit references so Python cannot collect an
        # in-flight animation before its ``finished`` signal is delivered.
        self._enter_animation: QPropertyAnimation | None = None
        self._close_animation: QPropertyAnimation | None = None

        # 阴影
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(16)
        shadow.setOffset(0, 4)
        shadow.setColor(rgba_token_to_qcolor(ThemeManager.instance().tokens.shadow_overlay))
        self.setGraphicsEffect(shadow)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(10)

        # 图标
        icon_label = QLabel()
        icon = IconRegistry.icon(kind if kind in ("success", "info", "warning", "error") else "info", size=18)
        icon_label.setPixmap(icon.pixmap(18, 18))
        icon_label.setObjectName("toastIcon")
        layout.addWidget(icon_label)

        # 消息
        self._msg_label = QLabel(message)
        self._msg_label.setObjectName("toastMessage")
        self._msg_label.setWordWrap(True)
        layout.addWidget(self._msg_label, 1)

        # 操作按钮
        if action_text and action_callback:
            self._action_btn: QPushButton | None = QPushButton(action_text)
            self._action_btn.setObjectName("toastAction")
            self._action_btn.setFlat(True)
            self._action_btn.clicked.connect(self._on_action)
            layout.addWidget(self._action_btn)
        else:
            self._action_btn = None

        # 关闭按钮
        self._close_btn = QPushButton()
        close_icon = IconRegistry.icon("close", size=14, color="muted")
        self._close_btn.setIcon(close_icon)
        self._close_btn.setObjectName("toastClose")
        self._close_btn.setFlat(True)
        self._close_btn.setFixedSize(20, 20)
        self._close_btn.clicked.connect(self._start_close)
        layout.addWidget(self._close_btn)

        # 倒计时定时器
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._start_close)
        # ``windowOpacity`` animations may not emit ``finished`` for a child
        # widget on every Qt platform/plugin.  The fallback keeps dismissal a
        # correctness guarantee; the animation is cosmetic only.
        self._close_fallback_timer = QTimer(self)
        self._close_fallback_timer.setSingleShot(True)
        self._close_fallback_timer.timeout.connect(self._finish_close_after_timeout)
        if duration > 0:
            self._timer.start(duration)

        # 入场动画
        self._opacity = 0.0
        self._offset_y = 20
        self.setWindowOpacity(0.0)

    # ------------------------------------------------------------------
    # 动画
    # ------------------------------------------------------------------

    def animate_in(self) -> None:
        """滑入 + 淡入。"""
        if self._closing:
            return
        self._opacity = 0.0
        self._offset_y = 20
        self.setWindowOpacity(0.0)
        self.adjustSize()
        self.show()

        # 使用 QPropertyAnimation 做淡入
        from PyQt6.QtCore import QEasingCurve
        self._stop_animation("_enter_animation")
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(200)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.finished.connect(lambda: self._clear_animation("_enter_animation", anim))
        self._enter_animation = anim
        anim.start()

    def _start_close(self) -> None:
        """开始关闭动画。"""
        if self._closing:
            return
        self._closing = True
        self._timer.stop()
        from PyQt6.QtCore import QEasingCurve
        self._stop_animation("_enter_animation")
        anim = QPropertyAnimation(self, b"windowOpacity", self)
        anim.setDuration(150)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setEasingCurve(QEasingCurve.Type.InCubic)
        anim.finished.connect(lambda: self._finish_close_animation(anim))
        self._close_animation = anim
        anim.start()
        self._close_fallback_timer.start(anim.duration() + 50)

    def _stop_animation(self, attribute: str) -> None:
        """Stop and dispose of an animation superseded by a new transition."""
        anim = getattr(self, attribute)
        if anim is None:
            return
        anim.stop()
        anim.deleteLater()
        setattr(self, attribute, None)

    def _clear_animation(self, attribute: str, anim: QPropertyAnimation) -> None:
        if getattr(self, attribute) is anim:
            setattr(self, attribute, None)
        anim.deleteLater()

    def _finish_close_animation(self, anim: QPropertyAnimation) -> None:
        if self._close_animation is not anim:
            return
        self._close_fallback_timer.stop()
        self._close_animation = None
        anim.deleteLater()
        self._finish_close()

    def _finish_close_after_timeout(self) -> None:
        """Complete dismissal when Qt cannot advance a child-opacity animation."""
        anim = self._close_animation
        if anim is None:
            return
        anim.stop()
        self._finish_close_animation(anim)

    def _finish_close(self) -> None:
        if not self._closing:
            return
        self.closed.emit()
        self.deleteLater()

    def _on_action(self) -> None:
        try:
            if self._action_callback:
                self._action_callback()
        finally:
            self._start_close()


class ToastOverlay(QWidget):
    """Toast 堆叠容器，吸附在父窗口右上角。

    管理多条 Toast 的垂直堆叠和自动清理。
    """

    def __init__(self, parent: QMainWindow) -> None:
        super().__init__(parent)
        # 设为透明背景但接收鼠标事件
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 16, 16)
        layout.setSpacing(8)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight)
        self._layout = layout
        self._toasts: list[Toast] = []
        self._max_toasts = 5
        self._dedup_window_ms = 500
        # Sliding window of recent messages for proper dedup (not just last one)
        self._recent_messages: deque[tuple[str, int]] = deque(maxlen=20)

        # 监听父窗口大小变化
        parent.installEventFilter(self)
        self._reposition()

    def eventFilter(self, obj, event) -> bool:
        from PyQt6.QtCore import QEvent
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move):
            self._reposition()
        return super().eventFilter(obj, event)

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        # 放在主窗口右上角，留出菜单/工具栏空间
        margin_top = 48
        margin_right = 8
        w = min(360, parent.width() // 2)
        self.setGeometry(
            parent.width() - w - margin_right,
            margin_top,
            w,
            parent.height() - margin_top - 16,
        )
        self.raise_()

    def show_toast(
        self,
        message: str,
        *,
        kind: str = "info",
        duration: int = 3000,
        action_text: str = "",
        action_callback: Callable[[], None] | None = None,
    ) -> Toast:
        """显示一条 Toast。"""
        # 去重：500ms 内相同消息不重复弹出（滑动窗口，不仅看最后一条）
        import time
        now_ms = int(time.monotonic() * 1000)
        # Prune expired entries
        while self._recent_messages and (now_ms - self._recent_messages[0][1]) > self._dedup_window_ms:
            self._recent_messages.popleft()
        # Check if message was seen recently
        if any(msg == message for msg, _ in self._recent_messages):
            return self._toasts[-1] if self._toasts else None  # type: ignore[return-value]
        self._recent_messages.append((message, now_ms))

        # 限流：超过最大数量时同步移除最旧的（而非仅启动异步动画）
        while len(self._toasts) >= self._max_toasts:
            oldest = self._toasts.pop(0)
            self._layout.removeWidget(oldest)
            oldest.deleteLater()

        toast = Toast(
            message,
            kind=kind,
            duration=duration,
            action_text=action_text,
            action_callback=action_callback,
            parent=self,
        )
        toast.closed.connect(lambda t=toast: self._remove(t))
        self._layout.insertWidget(0, toast)
        self._toasts.append(toast)
        toast.animate_in()
        self.show()
        return toast

    def _remove(self, toast: Toast) -> None:
        if toast in self._toasts:
            self._toasts.remove(toast)
        if not self._toasts:
            self.hide()


class ToastManager:
    """全局 Toast 管理器单例。

    在 MainWindow 初始化时绑定，提供便捷的静态/实例方法发送通知。
    """

    _instance: ToastManager | None = None

    def __init__(self) -> None:
        self._overlay: ToastOverlay | None = None

    @classmethod
    def instance(cls) -> ToastManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def bind(self, main_window: QMainWindow) -> None:
        """绑定到主窗口（在 MainWindow.__init__ 中调用一次）。"""
        self._overlay = ToastOverlay(main_window)
        self._overlay.hide()

    def show(
        self,
        message: str,
        *,
        kind: str = "info",
        duration: int = 3000,
        action_text: str = "",
        action_callback: Callable[[], None] | None = None,
    ) -> Toast | None:
        """显示 Toast 通知。"""
        if self._overlay is None:
            return None
        # 检查 overlay 的 C++ 对象是否已被析构（窗口关闭后）
        try:
            from PyQt6 import sip
            if sip.isdeleted(self._overlay):
                self._overlay = None
                return None
        except (ImportError, TypeError):
            pass
        overlay = self._overlay
        assert overlay is not None
        return overlay.show_toast(
            message,
            kind=kind,
            duration=duration,
            action_text=action_text,
            action_callback=action_callback,
        )

    def success(self, message: str, *, duration: int = 2500) -> Toast | None:
        return self.show(message, kind="success", duration=duration)

    def info(self, message: str, *, duration: int = 3000) -> Toast | None:
        return self.show(message, kind="info", duration=duration)

    def warning(self, message: str, *, duration: int = 4000) -> Toast | None:
        return self.show(message, kind="warning", duration=duration)

    def error(self, message: str, *, duration: int = 5000, action_text: str = "", action_callback=None) -> Toast | None:
        return self.show(message, kind="error", duration=duration, action_text=action_text, action_callback=action_callback)
