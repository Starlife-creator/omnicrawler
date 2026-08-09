"""帮助提示组件。

悬浮问号按钮，点击显示帮助提示文本。
支持悬停摘要、点击详细说明和 F1 上下文帮助。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QPushButton, QWidget

from ...services.help_registry import get_help
from ..design_system import RADIUS, ThemeManager
from ..i18n import _


class HelpTooltip(QPushButton):
    """悬浮问号提示按钮。

    提供至少 32×32 的点击区域，显示清晰图标，
    支持悬停摘要、点击详细说明和 F1 上下文帮助。
    所有颜色经设计令牌获取，自动跟随主题。
    """

    def __init__(
        self,
        help_id: str,
        parent: QWidget | None = None,
        *,
        mode: str = "simple",
        context: str = "",
    ) -> None:
        """初始化帮助提示按钮。

        Args:
            help_id: 帮助条目 ID。
            parent: 父组件。
            mode: 当前 UI 模式。
            context: 当前任务上下文建议。
        """
        super().__init__(_("?"), parent)
        self.help_id = help_id
        self._entry = get_help(help_id)
        self._mode = mode
        self._context = context

        # 至少 32×32 的点击区域，符合可访问性标准
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setObjectName("helpTooltip")

        # 悬停摘要
        short_text = self._entry.short(mode)
        self.setToolTip(
            f"<b>{self._entry.title}</b><br>{short_text}<br>" +

            _("<i>点击查看完整说明；按 F1 打开帮助中心</i>")
        )

        self.setAccessibleName(_("帮助：") + self._entry.title)
        self.setAccessibleDescription(self._entry.short(mode))
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.clicked.connect(self._show_help)

        # 应用令牌样式并监听主题切换
        self._apply_token_style()
        ThemeManager.instance().theme_changed.connect(self._apply_token_style)

    def _apply_token_style(self, *_args) -> None:
        """从设计令牌生成样式，自动跟随主题。"""
        t = ThemeManager.instance().tokens
        self.setStyleSheet(f"""
            QPushButton#helpTooltip {{
                background-color: {t.primary};
                color: #FFFFFF;
                border-radius: {RADIUS["pill"]}px;
                font-weight: bold;
                font-size: 15px;
                border: 1px solid {t.primary_active};
            }}
            QPushButton#helpTooltip:hover {{
                background-color: {t.primary_hover};
                border-color: {t.primary};
            }}
            QPushButton#helpTooltip:pressed {{
                background-color: {t.primary_active};
            }}
            QPushButton#helpTooltip:focus {{
                border: 2px solid {t.primary_hover};
                outline: none;
            }}
        """)

    def _show_help(self) -> None:
        """显示帮助提示。"""
        ancestor = self.parentWidget()
        while ancestor is not None:
            center = getattr(ancestor, "_help_center", None)
            if center is not None:
                center.show_help(self.help_id)
                return
            ancestor = ancestor.parentWidget()
        box = QMessageBox(self)
        box.setWindowTitle(self._entry.title)
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(self._entry.short(self._mode))
        box.setDetailedText(self._entry.full_text(self._mode, self._context))
        box.setStandardButtons(QMessageBox.StandardButton.Ok)
        box.exec()
