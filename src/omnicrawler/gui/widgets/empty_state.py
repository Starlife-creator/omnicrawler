"""空状态与"功能开发中"占位组件。

提供统一的空状态视觉：图标 + 标题 + 描述 + 操作引导按钮，
所有颜色经设计令牌获取，自动跟随主题。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..design_system import FONT_SIZE, RADIUS, SPACING, ThemeManager
from ..i18n import _


class EmptyState(QFrame):
    """统一的空状态/开发中占位组件。

    特性：
    - 居中大图标 + 标题 + 描述文字
    - 可选操作引导按钮
    - 虚线边框容器，颜色跟随设计令牌主题
    - 支持 reduced-motion
    """

    def __init__(
        self,
        icon: str = "🔧",
        title: str = "",
        description: str = "",
        parent: QWidget | None = None,
        *,
        action_label: str = "",
        action_callback=None,
    ) -> None:
        super().__init__(parent)
        self.setProperty("emptyState", True)
        self._action_btn: QPushButton | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            SPACING["xxl"], SPACING["xxl"], SPACING["xxl"], SPACING["xxl"],
        )
        layout.setSpacing(SPACING["md"])
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 图标
        self._icon_label = QLabel(icon)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setObjectName("emptyStateIcon")
        layout.addWidget(self._icon_label)

        # 标题
        self._title_label = QLabel(title or _("功能开发中"))
        self._title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._title_label.setObjectName("emptyStateTitle")
        layout.addWidget(self._title_label)

        # 描述
        self._desc_label = QLabel(description)
        self._desc_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._desc_label.setWordWrap(True)
        self._desc_label.setObjectName("emptyStateDesc")
        layout.addWidget(self._desc_label)

        # 操作按钮
        if action_label and action_callback:
            btn_row = QHBoxLayout()
            btn_row.addStretch()
            self._action_btn = QPushButton(action_label)
            self._action_btn.setProperty("primary", True)
            self._action_btn.clicked.connect(action_callback)
            btn_row.addWidget(self._action_btn)
            btn_row.addStretch()
            layout.addLayout(btn_row)

        layout.addStretch()

        # 应用令牌样式
        self._apply_style()
        ThemeManager.instance().theme_changed.connect(self._apply_style)

    def _apply_style(self, *_args) -> None:
        """从设计令牌生成空状态样式。"""
        t = ThemeManager.instance().tokens
        self.setStyleSheet(f"""
            QFrame[emptyState="true"] {{
                background: transparent;
                border: 2px dashed {t.border};
                border-radius: {RADIUS["lg"]}px;
            }}
            QLabel#emptyStateIcon {{
                font-size: {FONT_SIZE["hero"]}px;
                color: {t.muted};
            }}
            QLabel#emptyStateTitle {{
                font-size: {FONT_SIZE["title"]}px;
                font-weight: 700;
                color: {t.text};
            }}
            QLabel#emptyStateDesc {{
                font-size: {FONT_SIZE["body"]}px;
                color: {t.muted};
                max-width: 420px;
            }}
        """)

    def set_message(self, icon: str, title: str, description: str = "") -> None:
        """动态更新空状态内容。"""
        self._icon_label.setText(icon)
        self._title_label.setText(title)
        if description:
            self._desc_label.setText(description)
