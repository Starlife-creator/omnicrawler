"""日期选择器组件。

基于 QCalendarWidget 的弹出式日期选择器。
"""

from __future__ import annotations

import logging

from PyQt6.QtCore import QDate, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCalendarWidget,
    QDialog,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import _

logger = logging.getLogger(__name__)


class CalendarPopup(QDialog):
    """弹出式日历日期选择器。"""

    date_selected = pyqtSignal(str)  # "YYYY-MM-DD" 格式

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("选择日期"))
        self.setWindowFlags(Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setModal(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        self._calendar = QCalendarWidget()
        self._calendar.setGridVisible(True)
        self._calendar.clicked.connect(self._on_date_clicked)
        layout.addWidget(self._calendar)

        btn_layout = QHBoxLayout()
        today_btn = QPushButton(_("今天"))
        today_btn.clicked.connect(self._select_today)
        btn_layout.addWidget(today_btn)

        clear_btn = QPushButton(_("清除"))
        clear_btn.clicked.connect(self._clear)
        btn_layout.addWidget(clear_btn)

        cancel_btn = QPushButton(_("取消"))
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        layout.addLayout(btn_layout)

    def set_date(self, date_str: str | None) -> None:
        """设置当前选中日期。

        Args:
            date_str: "YYYY-MM-DD" 格式的日期字符串。
        """
        if date_str:
            try:
                qdate = QDate.fromString(date_str, "yyyy-MM-dd")
                if qdate.isValid():
                    self._calendar.setSelectedDate(qdate)
            except Exception:
                logger.debug("Failed to parse date string", exc_info=True)

    def _on_date_clicked(self, qdate: QDate) -> None:
        """日期点击处理。"""
        self.date_selected.emit(qdate.toString("yyyy-MM-dd"))
        self.accept()

    def _select_today(self) -> None:
        """选择今天。"""
        today = QDate.currentDate()
        self.date_selected.emit(today.toString("yyyy-MM-dd"))
        self.accept()

    def _clear(self) -> None:
        """清除选择。"""
        self.date_selected.emit("")
        self.accept()
