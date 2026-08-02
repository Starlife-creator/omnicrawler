"""日志控制台组件。

彩色日志显示控件，支持级别过滤、搜索高亮、日志导出和自动裁剪。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import (
    QAction,
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QMenu,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ..design_system import FONT_FAMILY_MONO, FONT_SIZE, RADIUS, ThemeManager
from ..i18n import _

logger = logging.getLogger(__name__)

MAX_BLOCKS = 5000
TRIM_HEAD = 2000


class LogHighlighter(QSyntaxHighlighter):
    """日志语法高亮器。颜色跟随设计令牌主题。"""

    def __init__(self, parent: QTextDocument) -> None:
        super().__init__(parent)
        self._formats: dict[str, QTextCharFormat] = {}
        self._refresh_formats()
        # 监听主题切换
        ThemeManager.instance().theme_changed.connect(self._on_theme_changed)

    def _refresh_formats(self) -> None:
        """从设计令牌刷新高亮格式。"""
        t = ThemeManager.instance().tokens
        colors = {
            "error": QColor(t.danger),
            "warn": QColor(t.warning),
            "info": QColor(t.muted),
        }
        for level, color in colors.items():
            fmt = QTextCharFormat()
            fmt.setForeground(color)
            self._formats[level] = fmt

    def _on_theme_changed(self, *_args) -> None:
        """主题变更时刷新格式。"""
        self._refresh_formats()

    def highlightBlock(self, text: str | None) -> None:
        if text is None:
            text = ""
        lower = text.lower()
        if "error" in lower or "exception" in lower or "traceback" in lower:
            self.setFormat(0, len(text), self._formats["error"])
        elif "warn" in lower:
            self.setFormat(0, len(text), self._formats["warn"])
        else:
            self.setFormat(0, len(text), self._formats["info"])


class LogConsole(QWidget):
    """彩色日志控制台。

    功能：
    - 接收 (message, level) 槽并追加日志。
    - 日志级别快速过滤按钮 (INFO/WARN/ERROR/ALL)。
    - 右键菜单：复制选中行、搜索高亮、导出日志。
    - 自动裁剪：超过 5000 行时删除前 2000 行。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        # 过滤按钮栏
        self._filter_bar = QHBoxLayout()
        self._filter_bar.setContentsMargins(0, 0, 0, 0)
        self._filter_bar.setSpacing(4)

        self._current_filter: str = "all"
        self._filter_buttons: dict[str, QPushButton] = {}

        for level, label in [("all", _("全部")), ("info", "INFO"), ("warn", "WARN"), ("error", "ERROR")]:
            btn = QPushButton(label)
            btn.setCheckable(True)
            btn.setFixedWidth(56)
            btn.setFixedHeight(24)
            btn.clicked.connect(lambda checked, lvl=level: self._set_filter(lvl))
            if level == "all":
                btn.setChecked(True)
            self._filter_bar.addWidget(btn)
            self._filter_buttons[level] = btn

        self._filter_bar.addStretch()

        # 清空按钮
        clear_btn = QPushButton(_("清空"))
        clear_btn.setFixedWidth(48)
        clear_btn.setFixedHeight(24)
        clear_btn.clicked.connect(self.clear)
        self._filter_bar.addWidget(clear_btn)

        # 日志文本编辑器
        self._editor = QTextEdit()
        self._editor.setReadOnly(True)
        self._editor.setObjectName("logConsole")
        self._editor.setFont(QFont(FONT_FAMILY_MONO.split(", ")[0], 10))
        # 高亮器
        doc = self._editor.document()
        assert doc is not None
        self._highlighter = LogHighlighter(doc)
        self._apply_token_style()
        ThemeManager.instance().theme_changed.connect(self._apply_token_style)

        self._editor.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._editor.customContextMenuRequested.connect(self._show_context_menu)

        # 布局
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(self._filter_bar)
        layout.addWidget(self._editor)

        # 自动裁剪定时器
        self._trim_timer = QTimer(self)
        self._trim_timer.setSingleShot(True)
        self._trim_timer.timeout.connect(self._do_trim)

        # 日志缓存（用于过滤）
        self._all_logs: list[tuple[str, str]] = []  # [(message, level), ...]
        self._search_term: str = ""

    def _apply_token_style(self, *_args) -> None:
        """从设计令牌生成日志控制台样式，自动跟随主题。"""
        t = ThemeManager.instance().tokens
        self._editor.setStyleSheet(f"""
            QTextEdit#logConsole {{
                background-color: {t.code_bg};
                color: {t.code_fg};
                border: 1px solid {t.code_border};
                border-radius: {RADIUS["sm"]}px;
                font-family: {FONT_FAMILY_MONO};
                font-size: {FONT_SIZE["small"]}px;
            }}
        """)

    @pyqtSlot(str, str)
    def append_log(self, message: str, level: str = "info") -> None:
        """追加日志行。

        Args:
            message: 日志消息。
            level: 日志级别 (info/warn/error)。
        """
        self._all_logs.append((message, level))

        # 根据过滤器决定是否显示
        if self._current_filter != "all" and level != self._current_filter:
            return

        self._append_to_editor(message, level)

    def _append_to_editor(self, message: str, level: str) -> None:
        """向编辑器追加文本。"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] [{level.upper():5s}] {message}"
        self._editor.append(line)

        # 检查是否需要裁剪
        doc = self._editor.document()
        assert doc is not None
        if doc.blockCount() > MAX_BLOCKS:
            self._trim_timer.start(100)

    def _do_trim(self) -> None:
        """执行日志裁剪。"""
        doc = self._editor.document()
        assert doc is not None
        count = doc.blockCount()
        if count > MAX_BLOCKS:
            cursor = QTextCursor(doc)
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            for _ in range(TRIM_HEAD):
                cursor.movePosition(
                    QTextCursor.MoveOperation.Down,
                    QTextCursor.MoveMode.KeepAnchor,
                )
            cursor.removeSelectedText()

    def _set_filter(self, level: str) -> None:
        """设置日志级别过滤器。"""
        self._current_filter = level
        for btn_level, btn in self._filter_buttons.items():
            btn.setChecked(btn_level == level)

        # 重新渲染
        self._editor.clear()
        for msg, lv in self._all_logs:
            if level == "all" or lv == level:
                self._append_to_editor(msg, lv)

    def clear(self) -> None:
        """清空日志。"""
        self._editor.clear()
        self._all_logs.clear()

    def _show_context_menu(self, pos) -> None:
        """显示右键菜单。"""
        menu = QMenu(self)

        copy_action = QAction(_("复制选中行"), self)
        copy_action.triggered.connect(self._copy_selected)
        menu.addAction(copy_action)

        search_action = QAction(_("搜索高亮"), self)
        search_action.triggered.connect(self._search_highlight)
        menu.addAction(search_action)

        menu.addSeparator()

        export_action = QAction(_("导出日志为 .txt"), self)
        export_action.triggered.connect(self.export_logs)
        menu.addAction(export_action)

        menu.exec(self._editor.mapToGlobal(pos))

    def _copy_selected(self) -> None:
        """复制选中文本。"""
        cursor = self._editor.textCursor()
        if cursor.hasSelection():
            clipboard = QApplication.clipboard()
            assert clipboard is not None
            clipboard.setText(cursor.selectedText())

    def _search_highlight(self) -> None:
        """搜索高亮。"""
        cursor = self._editor.textCursor()
        selected = cursor.selectedText().strip()
        if not selected:
            return
        self._search_term = selected
        # 重新选择所有匹配
        doc = self._editor.document()
        assert doc is not None
        highlight_cursor = QTextCursor(doc)
        fmt = QTextCharFormat()
        t = ThemeManager.instance().tokens
        fmt.setBackground(QColor(t.warning_bg))

        while True:
            highlight_cursor = doc.find(selected, highlight_cursor)
            if highlight_cursor.isNull():
                break
            highlight_cursor.mergeCharFormat(fmt)

    def export_logs(self) -> None:
        """导出日志为文本文件。"""
        filepath, _selected_filter = QFileDialog.getSaveFileName(
            self, _("导出日志"), "omnicrawler_gui_log.txt",
            _("文本文件 (*.txt)"),
        )
        if not filepath:
            return

        try:
            with open(filepath, "w", encoding="utf-8") as f:
                f.write("# OmniCrawler GUI 日志导出\n")
                f.write(f"# 导出时间: {datetime.now().isoformat()}\n")
                f.write("# ---- 以下日志已自动脱敏 ----\n")
                f.write("# 本日志已自动脱敏：URL 域名和查询参数、选择器内容已被隐藏\n")
                f.write("# 字段名称和统计信息保留不变\n")
                f.write("#\n\n")

                for msg, level in self._all_logs:
                    redacted = self._redact_log(msg)
                    f.write(f"[{level.upper():5s}] {redacted}\n")

        except Exception:
            logger.debug("Failed to export logs", exc_info=True)

    def _redact_log(self, text: str) -> str:
        """对日志文本进行隐私脱敏处理。

        脱敏规则：
        - URL 域名和查询参数替换
        - CSS/XPath/JSONPath 选择器替换
        - 字段名称保留
        """
        # 脱敏 URL（保留协议和路径结构，域名替换）
        text = re.sub(
            r'https?://[^\s\'"<>]+',
            lambda m: re.sub(
                r'(https?://)[^/\s\'"<>]+',
                r'\1***.com',
                m.group(0)
            ),
            text,
        )

        # 脱敏 URL 查询参数
        text = re.sub(
            r'(\?[^\s\'"<>]*)',
            '?[REDACTED]',
            text,
        )

        # 脱敏 CSS 选择器（在引号中或特定模式）
        text = re.sub(
            r'(selector[=:]\s*["\'])([^"\']+)(["\'])',
            r'\1[REDACTED]\3',
            text,
            flags=re.IGNORECASE,
        )

        # 脱敏 XPath
        text = re.sub(
            r'(//[^\s\'"<>\[\]]+(?:\[[^\]]*\])?)',
            lambda m: '[REDACTED]' if re.search(r'[=@]', m.group(1)) else m.group(1),
            text,
        )

        return text
