from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..services.help_registry import HelpEntry, contextual_advice, get_help, search_help
from .design_system import scaled_font_px
from .i18n import _
from .widgets.toast import ToastManager


class HelpCenterDock(QDockWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(_("帮助中心"), parent)
        self.setObjectName("help_center_dock")
        self.setAllowedAreas(Qt.DockWidgetArea.LeftDockWidgetArea | Qt.DockWidgetArea.RightDockWidgetArea)
        self._mode = "simple"
        self._task: dict[str, Any] = {}
        self._current_id = "task.intent"
        # 当前**正在显示**的条目：复制示例以它为准。
        # 此前复制走 `get_help(self._current_id)`——而 `_current_id` 在未知 id 时不更新，
        # 于是会静默复制「上一条」的示例（内容不对，但不报错，§A-8）。
        self._current_entry: HelpEntry | None = None
        body = QWidget()
        layout = QVBoxLayout(body)
        self.search = QLineEdit()
        self.search.setPlaceholderText(_("搜索：翻页、PDF、监测、Excel……"))
        self.search.setAccessibleName(_("搜索离线帮助"))
        self.search.textChanged.connect(self._refresh_results)
        layout.addWidget(self.search)
        self.results = QListWidget()
        self.results.currentRowChanged.connect(self._select_result)
        layout.addWidget(self.results, 1)
        self.title = QLabel()
        self.title.setStyleSheet(f"font-size: {scaled_font_px("subtitle")}px; font-weight: 600;")
        layout.addWidget(self.title)
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(False)
        layout.addWidget(self.details, 2)
        actions = QHBoxLayout()
        copy = QPushButton(_("复制示例"))
        copy.clicked.connect(self._copy_example)
        actions.addWidget(copy)
        actions.addStretch()
        layout.addLayout(actions)
        self.setWidget(body)
        self._matches: list[HelpEntry] = []
        self._refresh_results()

    def set_context(self, mode: str, task: dict[str, Any]) -> None:
        self._mode = mode
        self._task = dict(task)
        self.show_help(self._current_id, reveal=False)

    def show_help(self, help_id: str, *, reveal: bool = True) -> None:
        try:
            entry = get_help(help_id)
        except KeyError:
            # A22：未知帮助 ID 不崩溃——显示通用提示（registry 缺键时的兜底）
            entry = HelpEntry(
                help_id=help_id, title=_("帮助"), what=_("暂无可用的帮助内容。"),
                why=_("此条目尚未收录到帮助中心。"), how=_("可在帮助搜索框输入关键词检索其他主题。"),
                example="", limitations=_("无"), common_errors=_("无"),
                default_behavior=_("无"), change_impact=_("无"),
            )
        else:
            # S3.1.12：仅已知 id 写入 _current_id——复制示例不再 KeyError
            self._current_id = entry.help_id
        # 无论命中与否都记下「当前显示的是什么」，复制以此为准
        self._current_entry = entry
        self.title.setText(entry.title)
        advice = contextual_advice(help_id, self._task)
        self.details.setPlainText(entry.full_text(self._mode, advice))
        if reveal:
            self.show()
            self.raise_()

    def focus_search(self) -> None:
        self.show()
        self.raise_()
        self.search.setFocus()
        self.search.selectAll()

    def _refresh_results(self) -> None:
        self._matches = search_help(self.search.text(), mode=self._mode)
        self.results.clear()
        self.results.addItems([f"{entry.title}  ·  {entry.short(self._mode)}" for entry in self._matches])
        if self._matches:
            self.results.setCurrentRow(0)

    def _select_result(self, row: int) -> None:
        if 0 <= row < len(self._matches):
            self.show_help(self._matches[row].help_id, reveal=self.isVisible())

    def _copy_example(self) -> None:
        entry = self._current_entry
        example = entry.example if entry is not None else ""
        if not example:
            # 不静默复制空串：点了「复制示例」却什么都没进剪贴板，用户会以为按钮坏了
            ToastManager.instance().info(_("当前帮助条目没有可复制的示例"))
            return
        clipboard = QApplication.clipboard()
        assert clipboard is not None
        clipboard.setText(example)
