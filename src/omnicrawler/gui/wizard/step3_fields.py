"""字段选择相关对话框（五步向导退役后保留的复用组件）。

包含选择器类型判断、XPath 候选推荐、可视化选字段（VisualFieldDialog，被任务画布复用）
与智能提取（SmartExtractDialog）。
"""

from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any, Literal

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...extraction.field_designer import FieldCandidate, analyze_url
from ..i18n import _


def selector_kind(selector: str) -> Literal["css", "xpath"]:
    """判断选择器是 XPath 还是 CSS（默认 css）。"""
    stripped = (selector or "").strip()
    if not stripped:
        return "css"
    # XPath 通常以 / .// ( @ [ 或 // 开头；CSS 选择器不会
    if stripped.startswith(("/", ".//", "(", "@", "//")) or "[@" in stripped:
        return "xpath"
    return "css"


def suggest_xpath_candidates(tree: Any, samples: list[str], *, top_n: int = 3) -> list[tuple[str, str, float]]:
    """根据示例文本从已解析的 HTML 树推荐 XPath 候选（(text, xpath, similarity)）。"""
    candidates: list[tuple[str, str, float]] = []
    for elem in tree.iter():
        text = (elem.text or "").strip()
        if not text:
            continue
        # 只取直接文本（不包含子元素文本）
        if len(elem) == 0 or text:
            xpath = tree.getroottree().getpath(elem)

            # 计算与示例文本的相似度
            best_sim = 0.0
            for sample in samples:
                # 包含匹配
                if sample in text or text in sample:
                    best_sim = max(best_sim, 0.85)
                # 编辑距离相似度
                sim = SequenceMatcher(None, text.lower(), sample.lower()).ratio()
                best_sim = max(best_sim, sim)

            if best_sim > 0.6:
                candidates.append((text[:100], xpath, best_sim))

    # 按相似度排序，取前 N
    candidates.sort(key=lambda x: x[2], reverse=True)
    return candidates[:top_n]


class VisualFieldThread(QThread):
    result_ready = pyqtSignal(list)
    error_occurred = pyqtSignal(str)

    def __init__(self, url: str) -> None:
        super().__init__()
        self._url = url

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            candidates = analyze_url(self._url, limit=150)
            if not self.isInterruptionRequested():
                self.result_ready.emit(candidates)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.error_occurred.emit(str(exc))


class VisualFieldDialog(QDialog):
    """One-click public-page analysis with plain-language field previews."""

    def __init__(self, url: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("可视化选字段"))
        self.setMinimumSize(900, 560)
        self._thread: VisualFieldThread | None = None
        self._candidates: list[FieldCandidate] = []
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(_("输入网页地址，系统会列出最稳定、最像业务字段的内容；选中需要的行后点击“添加字段”。")))
        line = QHBoxLayout()
        self._url = QLineEdit(url)
        load = QPushButton(_("分析网页"))
        load.clicked.connect(self._load)
        line.addWidget(self._url)
        line.addWidget(load)
        layout.addLayout(line)
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels([_("建议名称"), _("网页内容预览"), _("CSS 选择器"), _("属性"), _("稳定度")])
        header = self._table.horizontalHeader()
        assert header is not None
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        layout.addWidget(self._table)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert ok_button is not None
        ok_button.setText(_("添加字段"))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        if url:
            self._load()

    @property
    def selected_candidates(self) -> list[FieldCandidate]:
        rows = sorted({index.row() for index in self._table.selectedIndexes()})
        return [self._candidates[row] for row in rows if row < len(self._candidates)]

    def _load(self) -> None:
        url = self._url.text().strip()
        if not url:
            QMessageBox.information(self, _("提示"), _("请先输入 http:// 或 https:// 网页地址。"))
            return
        self._table.setRowCount(0)
        self._thread = VisualFieldThread(url)
        self._thread.setParent(self)
        self._thread.result_ready.connect(self._show_results)
        self._thread.error_occurred.connect(lambda message: QMessageBox.warning(self, _("分析失败"), message))
        self._thread.start()

    def _show_results(self, candidates: list) -> None:
        self._candidates = candidates
        self._table.setRowCount(len(candidates))
        for row, item in enumerate(candidates):
            for column, value in enumerate((item.suggested_name, item.text, item.css, item.attribute or "", f"{item.score:.0%}")):
                self._table.setItem(row, column, QTableWidgetItem(str(value)))
        if candidates:
            self._table.selectRow(0)
        else:
            QMessageBox.information(self, _("未找到字段"), _("该页面没有可读字段；可尝试浏览器模式或操作录制。"))


class SmartExtractDialog(QDialog):
    """智能提取对话框。

    粘贴 HTML 和示例文本，自动推荐最匹配的 XPath。
    另提供智能推荐（启发式）模式：粘贴 HTML → 本地离线规则（meta/h1-h3 匹配）
    猜测字段位置。该模式**不会调用任何大模型**，仅为规则匹配结果。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("智能提取 — XPath 推荐"))
        self.setMinimumSize(650, 550)

        layout = QVBoxLayout(self)

        # 模式切换
        mode_row = QHBoxLayout()
        self._mode_group = QButtonGroup(self)
        self._selector_mode_btn = QRadioButton(_("选择器模式"))
        self._selector_mode_btn.setChecked(True)
        # B7：原文案为"AI 模式"，实为离线启发式规则，改名避免误导
        self._ai_mode_btn = QRadioButton(_("智能推荐(启发式)"))
        self._ai_mode_btn.setToolTip(
            _("离线规则匹配（meta / h1-h3 等），不调用大模型，结果需人工核对。")
        )
        self._mode_group.addButton(self._selector_mode_btn, 1)
        self._mode_group.addButton(self._ai_mode_btn, 2)
        mode_row.addWidget(self._selector_mode_btn)
        mode_row.addWidget(self._ai_mode_btn)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        # HTML 输入
        layout.addWidget(QLabel(_("粘贴目标网页的 HTML 代码:")))
        self._html_edit = QPlainTextEdit()
        self._html_edit.setPlaceholderText(_("<html>...</html>"))
        layout.addWidget(self._html_edit)

        # 智能推荐(启发式) 模式：字段定义区域
        self._ai_fields_group = QGroupBox(_("智能推荐字段定义（离线规则）"))
        ai_fields_layout = QVBoxLayout(self._ai_fields_group)
        heuristic_note = QLabel(
            _("说明：本模式使用本地启发式规则匹配，不调用大模型；结果仅供参考，请核对后使用。")
        )
        heuristic_note.setWordWrap(True)
        ai_fields_layout.addWidget(heuristic_note)
        ai_fields_layout.addWidget(QLabel(_("每行一个字段：字段名=描述（如: title=文章标题）")))
        self._ai_fields_edit = QPlainTextEdit()
        self._ai_fields_edit.setPlaceholderText(_("title=文章标题\nauthor=作者名\ndate=发布日期"))
        self._ai_fields_edit.setMaximumHeight(80)
        ai_fields_layout.addWidget(self._ai_fields_edit)
        layout.addWidget(self._ai_fields_group)

        # 选择器模式：示例文本
        self._sample_group = QGroupBox(_("示例文本"))
        sample_layout = QVBoxLayout(self._sample_group)
        sample_layout.addWidget(QLabel(_("输入希望提取的示例文本（一行一个）:")))
        self._sample_edit = QPlainTextEdit()
        self._sample_edit.setPlaceholderText(_("示例文本1\n示例文本2"))
        self._sample_edit.setMaximumHeight(80)
        sample_layout.addWidget(self._sample_edit)
        layout.addWidget(self._sample_group)

        # 分析按钮
        analyze_btn = QPushButton(_("分析推荐 XPath"))
        analyze_btn.clicked.connect(self._analyze)
        layout.addWidget(analyze_btn)

        # 结果
        layout.addWidget(QLabel(_("推荐结果:")))
        self._result_table = QTableWidget(0, 3)
        self._result_table.setHorizontalHeaderLabels([_("相似度"), _("提取文本"), _("XPath")])
        result_header = self._result_table.horizontalHeader()
        assert result_header is not None
        result_header.setStretchLastSection(True)
        self._result_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._result_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        layout.addWidget(self._result_table)

        # 按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # B8：未选中有效结果行时禁用"确定"，避免把空 XPath 绑定到字段
        ok_button = buttons.button(QDialogButtonBox.StandardButton.Ok)
        assert ok_button is not None
        self._ok_button: QPushButton = ok_button
        self._ok_button.setToolTip(_("请先在推荐结果中选择一行"))
        self._result_table.itemSelectionChanged.connect(self._update_ok_enabled)
        self._update_ok_enabled()

        self._selected_xpath: str = ""
        self._selected_text: str = ""

        # 模式切换联动
        self._mode_group.buttonClicked.connect(self._on_mode_changed)
        self._on_mode_changed(self._selector_mode_btn)

    def _current_xpath(self) -> str:
        """返回当前选中结果行的 XPath（未选中或为空时返回空串）。"""
        row = self._result_table.currentRow()
        if row < 0:
            return ""
        item = self._result_table.item(row, 2)
        return item.text().strip() if item else ""

    def _update_ok_enabled(self) -> None:
        """仅当选中行带有非空 XPath 时才允许确认。"""
        self._ok_button.setEnabled(bool(self._current_xpath()))

    @property
    def selected_xpath(self) -> str:
        return self._selected_xpath

    @property
    def selected_text(self) -> str:
        return self._selected_text

    def _on_mode_changed(self, button: QRadioButton) -> None:
        """切换选择器 / 智能推荐(启发式) 模式时显示/隐藏对应区域。"""
        is_ai = button is self._ai_mode_btn
        self._sample_group.setVisible(not is_ai)
        self._ai_fields_group.setVisible(is_ai)

    def _analyze(self) -> None:
        """分析 HTML 并推荐 XPath（选择器模式）或启发式规则匹配（智能推荐模式）。"""
        html_text = self._html_edit.toPlainText().strip()

        if not html_text:
            QMessageBox.warning(self, _("提示"), _("请先粘贴 HTML 代码"))
            return

        if self._ai_mode_btn.isChecked():
            self._analyze_ai(html_text)
            return

        sample_text = self._sample_edit.toPlainText().strip()
        if not sample_text:
            QMessageBox.warning(self, _("提示"), _("请输入示例文本"))
            return

        samples = [s.strip() for s in sample_text.splitlines() if s.strip()]

        try:
            from lxml import html
            # 尝试解析 HTML（宽松模式），返回 HtmlElement 以支持 getpath/text_content/cssselect
            tree = html.fromstring(html_text)
        except ImportError:
            QMessageBox.warning(
                self, _("依赖缺失"),
                _("lxml 未安装，请运行 pip install omnicrawler-platform[html]"),
            )
            return
        except Exception as e:
            QMessageBox.warning(self, _("解析失败"), f"HTML 解析错误: {e}")
            return

        top = suggest_xpath_candidates(tree, samples)

        self._result_table.setRowCount(len(top))
        for i, (text, xpath, sim) in enumerate(top):
            pct = f"{sim * 100:.0f}%"
            self._result_table.setItem(i, 0, QTableWidgetItem(pct))
            self._result_table.setItem(i, 1, QTableWidgetItem(text))
            self._result_table.setItem(i, 2, QTableWidgetItem(xpath))

        self._update_ok_enabled()

        if not top:
            QMessageBox.information(
                self, _("未找到匹配"),
                _("未找到匹配内容，请检查示例文本是否存在于粘贴的 HTML 中。"),
            )
        else:
            self._result_table.resizeColumnsToContents()

    def _analyze_ai(self, html_text: str) -> None:
        """智能推荐(启发式)：用本地规则在 HTML 中猜测字段位置（不调用 LLM）。"""
        fields_text = self._ai_fields_edit.toPlainText().strip()
        if not fields_text:
            QMessageBox.warning(self, _("提示"), _("请输入要提取的字段定义"))
            return

        # 解析字段
        fields: list = []
        for line in fields_text.splitlines():
            line = line.strip()
            if "=" in line:
                name, _sep, desc = line.partition("=")
                fields.append({"name": name.strip(), "description": desc.strip()})
            elif line:
                fields.append({"name": line, "description": ""})

        if not fields:
            QMessageBox.warning(self, _("提示"), _("无法解析字段定义"))
            return

        # 离线启发式规则匹配（若需真实 LLM 提取，请使用 ai_graph.AIGraphExtractor）
        from lxml import html
        tree = html.fromstring(html_text)

        self._result_table.setRowCount(len(fields))
        for i, field in enumerate(fields):
            name = field["name"]
            # 启发式搜索：找标题、meta、h1-h3 等
            text = ""
            xpath = ""
            try:
                # 尝试从 meta 标签查找（XPath 变量绑定，字段名含引号也不会崩溃）
                for meta in tree.xpath(
                    "//meta[contains(@name, $field) or contains(@property, $field)]",
                    field=name,
                ):
                    text = meta.get("content", "")
                    xpath = tree.getroottree().getpath(meta)
                    break

                # 尝试从 h1-h3 查找
                if not text:
                    for h in tree.xpath(
                        "//h1[contains(text(), $field)] | //h2[contains(text(), $field)] | //h3[contains(text(), $field)]",
                        field=name,
                    ):
                        text = (h.text_content() or "").strip()[:100]
                        xpath = tree.getroottree().getpath(h)
                        break
            except Exception:
                # XPathEvalError 等：字段名特殊字符导致表达式无法求值时降级为未匹配
                text = ""
                xpath = ""

            if text:
                # 不再显示伪造的 90% 置信度，如实标注为规则命中
                self._result_table.setItem(i, 0, QTableWidgetItem(_("规则命中")))
                self._result_table.setItem(i, 1, QTableWidgetItem(text[:80]))
                self._result_table.setItem(i, 2, QTableWidgetItem(xpath))
            else:
                self._result_table.setItem(i, 0, QTableWidgetItem("—"))
                self._result_table.setItem(i, 1, QTableWidgetItem(_("未找到匹配")))
                self._result_table.setItem(i, 2, QTableWidgetItem(""))

        self._result_table.setHorizontalHeaderLabels(
            [_("规则匹配"), _("提取文本"), _("XPath 位置")]
        )
        self._result_table.resizeColumnsToContents()
        self._update_ok_enabled()

    def accept(self) -> None:
        """确认选择。

        B8：未选中结果行（或该行 XPath 为空）时拒绝确认并提示，
        避免把空 XPath 绑定到字段上。
        """
        xpath = self._current_xpath()
        if not xpath:
            QMessageBox.warning(
                self, _("提示"),
                _("请先在推荐结果中选中一行有效的 XPath 后再确认；若无结果请先执行分析。"),
            )
            return
        row = self._result_table.currentRow()
        self._selected_xpath = xpath
        text_item = self._result_table.item(row, 1)
        self._selected_text = text_item.text() if text_item else ""
        super().accept()
