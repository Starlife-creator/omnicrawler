"""Step 2: 种子 URL 和分页配置页面。

输入种子 URL、分页规则和增量抓取选项。
支持占位符高亮和一键从剪贴板填充。
"""

from __future__ import annotations

import re

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat
from PyQt6.QtWidgets import (
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from ...services.help_registry import get_help
from ..core.config_model import CrawlConfig
from ..design_system import ThemeManager
from ..i18n import _
from ..widgets.form_feedback import clear_error_style, set_error_style, shake_widget
from ..widgets.help_tooltip import HelpTooltip


def _label_with_help(text: str, key: str) -> QWidget:
    """创建带帮助提示的标签行。"""
    widget = QWidget()
    row = QHBoxLayout(widget)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(QLabel(text))
    row.addWidget(HelpTooltip(key))
    row.addStretch()
    return widget


class PlaceholderHighlighter(QSyntaxHighlighter):
    """高亮显示模板占位符 {{...}}。颜色跟随设计令牌主题。"""

    def __init__(self, parent) -> None:
        super().__init__(parent)
        self._refresh_format()
        ThemeManager.instance().theme_changed.connect(self._on_theme_changed)

    def _refresh_format(self) -> None:
        t = ThemeManager.instance().tokens
        self._fmt = QTextCharFormat()
        self._fmt.setForeground(QColor(t.danger))
        self._fmt.setFontWeight(700)
        self._fmt.setUnderlineStyle(
            QTextCharFormat.UnderlineStyle.SpellCheckUnderline
        )
        self._fmt.setUnderlineColor(QColor(t.danger))

    def _on_theme_changed(self, *_args) -> None:
        self._refresh_format()

    def highlightBlock(self, text: str | None) -> None:
        if text is None:
            return
        for match in re.finditer(r"\{\{.*?\}\}", text):
            self.setFormat(match.start(), match.end() - match.start(), self._fmt)


class Step2UrlsPage(QWizardPage):
    """Step 2: 输入种子 URL 和分页规则。"""

    config_changed = pyqtSignal()
    inspect_requested = pyqtSignal(str)
    record_requested = pyqtSignal(str)

    def __init__(self, config: CrawlConfig, parent: QWizardPage | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._updating = False

        self.setTitle(_("步骤 2/5：可选：补充范围与高级控制"))
        self.setSubTitle(_("第一页已带入入口和建议范围；只有多个入口、页码规则或性能要求不同时才需要修改。"))
        self.setAccessibleName(_("Step 2: 网址与范围"))
        self.setAccessibleDescription(_("Step 2 of the OmniCrawler configuration wizard"))

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        scroll.setWidget(content)
        outer_layout.addWidget(scroll)

        # ---- 种子 URL ----
        url_group = QGroupBox(_("补充更多入口（可选）"))
        url_layout = QVBoxLayout(url_group)

        self._url_edit = QPlainTextEdit()
        self._url_edit.setPlaceholderText(_("每行输入一个 URL，例如:\nhttps://example.com/news\nhttps://example.com/articles?page=1"))
        self._url_edit.setMaximumHeight(120)
        self._url_edit.textChanged.connect(self._on_data_changed)
        self._highlighter = PlaceholderHighlighter(self._url_edit.document())
        url_layout.addWidget(self._url_edit)

        # 占位符提示和按钮
        hint_layout = QHBoxLayout()
        self._placeholder_hint = QLabel()
        self._placeholder_hint.setObjectName("placeholderHint")
        self._placeholder_hint.setProperty("status", "danger")
        self._placeholder_hint.setVisible(False)
        hint_layout.addWidget(self._placeholder_hint)

        self._paste_btn = QPushButton(_("从剪贴板填充"))
        self._paste_btn.setFixedWidth(120)
        self._paste_btn.clicked.connect(self._paste_from_clipboard)
        hint_layout.addWidget(self._paste_btn)
        self._inspect_btn = QPushButton(_("智能识别"))
        self._inspect_btn.setToolTip(_("安全探测第一个网址并推荐模板、分页和抓取方式"))
        self._inspect_btn.clicked.connect(self._request_inspection)
        hint_layout.addWidget(self._inspect_btn)
        self._record_btn = QPushButton(_("学习点击/搜索/翻页"))
        self._record_btn.setToolTip(_help_text("source.pagination"))
        self._record_btn.clicked.connect(self._request_recording)
        hint_layout.addWidget(self._record_btn)
        hint_layout.addWidget(HelpTooltip("source.seed"))
        hint_layout.addStretch()
        url_layout.addLayout(hint_layout)

        layout.addWidget(url_group)

        # ---- 爬取参数 ----
        self._crawl_group = QGroupBox(_("采集数量与速度（高级，可选）"))
        crawl_form = QFormLayout(self._crawl_group)

        self._max_pages = QSpinBox()
        self._max_pages.setRange(1, 100000)
        self._max_pages.setValue(10)
        self._max_pages.valueChanged.connect(self._on_data_changed)
        crawl_form.addRow(_label_with_help(_("最大页数:"), "crawl.max_pages"), self._max_pages)

        self._delay = QSpinBox()
        self._delay.setRange(0, 60)
        self._delay.setValue(1)
        self._delay.setSuffix(_(" 秒"))
        self._delay.valueChanged.connect(self._on_data_changed)
        crawl_form.addRow(_label_with_help(_("请求延迟:"), "http.delay"), self._delay)

        self._concurrency = QSpinBox()
        self._concurrency.setRange(1, 16)
        self._concurrency.setValue(2)
        self._concurrency.valueChanged.connect(self._on_data_changed)
        crawl_form.addRow(_label_with_help(_("并发数:"), "crawl.concurrency"), self._concurrency)

        layout.addWidget(self._crawl_group)

        # ---- 分页（可选） ----
        self._pagination_group = QGroupBox(_("手动页码规则（高级，可选）"))
        pagination_form = QFormLayout(self._pagination_group)

        self._pagination_param = QLineEdit()
        self._pagination_param.setPlaceholderText("page")
        self._pagination_param.textChanged.connect(self._on_data_changed)
        pagination_form.addRow(_("分页参数名:"), self._pagination_param)

        self._pagination_start = QSpinBox()
        self._pagination_start.setRange(0, 10000)
        self._pagination_start.setValue(1)
        self._pagination_start.valueChanged.connect(self._on_data_changed)
        pagination_form.addRow(_("起始页码:"), self._pagination_start)

        self._pagination_end = QSpinBox()
        self._pagination_end.setRange(0, 100000)
        self._pagination_end.setValue(10)
        self._pagination_end.valueChanged.connect(self._on_data_changed)
        pagination_form.addRow(_("结束页码:"), self._pagination_end)
        pagination_form.addRow(HelpTooltip("source.pagination"))

        layout.addWidget(self._pagination_group)

        # ---- 增量抓取（折叠，标记实验性） ----
        self._incremental_group = QGroupBox(_("同址变化监测（可选）"))
        self._incremental_group.setCheckable(True)
        self._incremental_group.setChecked(False)
        self._incremental_group.toggled.connect(self._on_data_changed)
        incremental_form = QFormLayout(self._incremental_group)

        self._since_date = QLineEdit()
        self._since_date.setPlaceholderText("2026-01-01")
        self._since_date.textChanged.connect(self._on_data_changed)
        incremental_form.addRow(_("起始日期 (YYYY-MM-DD):"), self._since_date)

        incremental_form.addRow(HelpTooltip("updates.same_url"))
        layout.addWidget(self._incremental_group)

        layout.addStretch()

    def set_inspecting(self, active: bool) -> None:
        self._inspect_btn.setEnabled(not active)
        self._inspect_btn.setText(_("正在识别…") if active else _("智能识别"))

    def _request_inspection(self) -> None:
        values = [line.strip() for line in self._url_edit.toPlainText().splitlines() if line.strip()]
        if not values:
            QMessageBox.information(self, _("提示"), _("请先输入一个网址"))
            return
        self.inspect_requested.emit(values[0])

    def _request_recording(self) -> None:
        values = [line.strip() for line in self._url_edit.toPlainText().splitlines() if line.strip()]
        if not values:
            QMessageBox.information(self, _("提示"), _("请先输入浏览器地址栏中的栏目入口网址"))
            return
        self.record_requested.emit(values[0])

    def set_simple_mode(self, enabled: bool) -> None:
        """Hide hand-authored request mechanics while preserving guided learning."""
        self._pagination_group.setVisible(not enabled)
        self._delay.setVisible(not enabled)
        self._concurrency.setVisible(not enabled)
        form = self._crawl_group.layout()
        if isinstance(form, QFormLayout):
            for widget in (self._delay, self._concurrency):
                label = form.labelForField(widget)
                if label:
                    label.setVisible(not enabled)

    def initializePage(self) -> None:
        """加载当前配置。"""
        self._clear_validation()
        self._updating = True
        self._url_edit.setPlainText("\n".join(self._config.seed_urls))
        self._max_pages.setValue(self._config.max_pages)
        self._delay.setValue(int(self._config.delay))
        self._concurrency.setValue(self._config.concurrency)

        if self._config.pagination:
            self._pagination_param.setText(str(self._config.pagination.get("parameter", self._config.pagination.get("param", ""))))
            self._pagination_start.setValue(int(self._config.pagination.get("start", 1)))
            self._pagination_end.setValue(int(self._config.pagination.get("end", self._config.max_pages)))

        # 增量
        self._incremental_group.setChecked(self._config.incremental or self._config.monitor_same_url)
        if self._config.since_date:
            self._since_date.setText(self._config.since_date)

        self._check_placeholders()
        self._updating = False

    def _clear_validation(self) -> None:
        """清除之前的验证错误样式。"""
        clear_error_style(self._url_edit)

    def validatePage(self) -> bool:
        """校验并保存。"""
        urls = self._get_urls()
        if not urls:
            shake_widget(self._url_edit, self)
            set_error_style(self._url_edit, _("请至少输入一个种子 URL"))
            return False

        # 检查占位符
        if any("{{" in u and "}}" in u for u in urls):
            reply = QMessageBox.question(
                self, _("存在未替换占位符"),
                _("种子 URL 中包含未替换的模板占位符 {{...}}。\n是否仍然继续？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.No:
                return False

        self._save_to_config()
        return True

    def _get_urls(self) -> list[str]:
        """从文本框获取 URL 列表。"""
        text = self._url_edit.toPlainText().strip()
        if not text:
            return []
        return [line.strip() for line in text.splitlines() if line.strip()]

    def _save_to_config(self) -> None:
        """保存到配置。"""
        self._config.seed_urls = self._get_urls()
        self._config.max_pages = self._max_pages.value()
        self._config.delay = float(self._delay.value())
        self._config.concurrency = self._concurrency.value()

        # 分页
        param = self._pagination_param.text().strip()
        if param:
            self._config.pagination = {
                "type": "page",
                "parameter": param,
                "start": self._pagination_start.value(),
                "end": max(self._pagination_start.value(), self._pagination_end.value()),
            }
        else:
            self._config.pagination = None

        # 增量
        self._config.incremental = self._incremental_group.isChecked()
        self._config.monitor_same_url = self._incremental_group.isChecked()
        self._config.since_date = self._since_date.text().strip() or None

    def _check_placeholders(self) -> None:
        """检查并高亮占位符。"""
        text = self._url_edit.toPlainText()
        has_placeholders = bool(re.search(r"\{\{.*?\}\}", text))
        self._placeholder_hint.setVisible(has_placeholders)
        if has_placeholders:
            self._placeholder_hint.setText(_("⚠ 请将 {{...}} 占位符替换为真实网址"))
        self._highlighter.rehighlight()

    def _on_data_changed(self) -> None:
        """数据变更处理。"""
        if self._updating:
            return
        self._check_placeholders()
        self._save_to_config()
        self.config_changed.emit()

    def _paste_from_clipboard(self) -> None:
        """从剪贴板粘贴 URL 列表。"""
        from PyQt6.QtWidgets import QApplication
        clipboard = QApplication.clipboard()
        assert clipboard is not None
        text = clipboard.text().strip()
        if not text:
            QMessageBox.information(self, _("提示"), _("剪贴板为空"))
            return

        # 尝试替换占位符
        current = self._url_edit.toPlainText()
        lines = current.splitlines()
        paste_lines = text.splitlines()

        # 按顺序替换占位符
        placeholders = [i for i, line in enumerate(lines) if "{{" in line and "}}" in line]
        if placeholders and paste_lines:
            for idx, pl_idx in enumerate(placeholders):
                if idx < len(paste_lines):
                    lines[pl_idx] = paste_lines[idx]
            self._url_edit.setPlainText("\n".join(lines))
        else:
            # 直接追加
            if current:
                self._url_edit.setPlainText(current + "\n" + text)
            else:
                self._url_edit.setPlainText(text)

        self._check_placeholders()


def _help_text(key: str) -> str:
    entry = get_help(key)
    return f"{entry.summary}\n\n{entry.details}" + (f"\n\n示例：{entry.example}" if entry.example else "")
