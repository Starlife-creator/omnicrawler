"""空状态与"功能开发中"占位组件。

提供统一的空状态视觉：图标 + 标题 + 描述 + 操作引导按钮，
所有颜色经设计令牌获取，自动跟随主题。
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QTableWidget,
    QTreeWidget,
    QVBoxLayout,
    QWidget,
)

from ..design_system import RADIUS, SPACING, ThemeManager, scaled_font_px
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
        action_callback: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        # 无障碍：空态的名字＝其标题；set_message 会随状态切换同步更新
        self.setAccessibleName(title or _("空状态"))
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

    def _apply_style(self, *_args: object) -> None:
        """从设计令牌生成空状态样式。"""
        t = ThemeManager.instance().tokens
        self.setStyleSheet(f"""
            QFrame[emptyState="true"] {{
                background: transparent;
                border: 2px dashed {t.border};
                border-radius: {RADIUS["lg"]}px;
            }}
            QLabel#emptyStateIcon {{
                font-size: {scaled_font_px("hero")}px;
                color: {t.muted};
            }}
            QLabel#emptyStateTitle {{
                font-size: {scaled_font_px("title")}px;
                font-weight: 700;
                color: {t.text};
            }}
            QLabel#emptyStateDesc {{
                font-size: {scaled_font_px("body")}px;
                color: {t.muted};
                max-width: 420px;
            }}
        """)

    def set_message(self, icon: str, title: str, description: str = "") -> None:
        """动态更新空状态内容（空描述会清空并隐藏描述行）。

        2026-09-11 修正：原实现 `if description:` 使得「从有描述切到无描述」时残留旧文案，
        BaseView 的三态切换依赖本方法，故改为总是写入并据内容显隐。
        """
        self._icon_label.setText(icon)
        self._title_label.setText(title)
        self._desc_label.setText(description)
        self.setAccessibleName(title or _("空状态"))
        self._desc_label.setVisible(bool(description.strip()))


def _item_count(list_widget: QWidget) -> int:
    """列表控件的项目数。

    ★ 不认识的类型**报错**而不是当作 0：静默当成空会伪造一个"没有数据"的结论。
    """
    if isinstance(list_widget, QListWidget):
        return list_widget.count()
    if isinstance(list_widget, QTableWidget):
        return list_widget.rowCount()
    if isinstance(list_widget, QTreeWidget):
        return list_widget.topLevelItemCount()
    raise TypeError(_("空态同步不支持这种列表控件：{0}").format(type(list_widget).__name__))


def sync_list_empty_state(list_widget: QWidget, empty_state: QWidget) -> int:
    """按列表项数同步「列表 / 空态」的可见性，返回当前项数（V2 空态统一）。

    ★ 两者**互斥**：同时可见会让用户既看到"还没有内容"、又看到一张空表。
    """
    count = _item_count(list_widget)
    list_widget.setVisible(count > 0)
    empty_state.setVisible(count == 0)
    return count
