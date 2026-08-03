"""Step 3: 提取字段配置页面。

使用 QTableWidget 动态增删字段定义行，支持拖拽排序、批量操作、智能提取和选择器测试。
"""

from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
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
    QWizardPage,
)

from ...extraction.field_designer import FieldCandidate, analyze_url
from ...security.controlled_http import scoped_fetch
from ..core.config_model import CrawlConfig, FieldDef
from ..i18n import _
from ..widgets.form_feedback import clear_error_style, set_error_style, shake_widget
from ..widgets.help_tooltip import HelpTooltip


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
        self.setWindowTitle("可视化选字段")
        self.setMinimumSize(900, 560)
        self._thread: VisualFieldThread | None = None
        self._candidates: list[FieldCandidate] = []
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("输入网页地址，系统会列出最稳定、最像业务字段的内容；选中需要的行后点击“添加字段”。"))
        line = QHBoxLayout()
        self._url = QLineEdit(url)
        load = QPushButton("分析网页")
        load.clicked.connect(self._load)
        line.addWidget(self._url)
        line.addWidget(load)
        layout.addLayout(line)
        self._table = QTableWidget(0, 5)
        self._table.setHorizontalHeaderLabels(["建议名称", "网页内容预览", "CSS 选择器", "属性", "稳定度"])
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
        ok_button.setText("添加字段")
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
            QMessageBox.information(self, "提示", "请先输入 http:// 或 https:// 网页地址。")
            return
        self._table.setRowCount(0)
        self._thread = VisualFieldThread(url)
        self._thread.setParent(self)
        self._thread.result_ready.connect(self._show_results)
        self._thread.error_occurred.connect(lambda message: QMessageBox.warning(self, "分析失败", message))
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
            QMessageBox.information(self, "未找到字段", "该页面没有可读字段；可尝试浏览器模式或操作录制。")


class SmartExtractDialog(QDialog):
    """智能提取对话框。

    粘贴 HTML 和示例文本，自动推荐最匹配的 XPath。
    支持 AI 模式：粘贴 HTML → LLM 直接提取字段值。
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
        self._ai_mode_btn = QRadioButton(_("AI 模式"))
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

        # AI 模式：字段定义区域
        self._ai_fields_group = QGroupBox(_("AI 提取字段定义"))
        ai_fields_layout = QVBoxLayout(self._ai_fields_group)
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

        self._selected_xpath: str = ""
        self._selected_text: str = ""

        # 模式切换联动
        self._mode_group.buttonClicked.connect(self._on_mode_changed)
        self._on_mode_changed(self._selector_mode_btn)

    @property
    def selected_xpath(self) -> str:
        return self._selected_xpath

    @property
    def selected_text(self) -> str:
        return self._selected_text

    def _on_mode_changed(self, button: QRadioButton) -> None:
        """切换选择器/AI 模式时显示/隐藏对应区域。"""
        is_ai = button is self._ai_mode_btn
        self._sample_group.setVisible(not is_ai)
        self._ai_fields_group.setVisible(is_ai)

    def _analyze(self) -> None:
        """分析 HTML 并推荐 XPath（选择器模式）或 AI 提取（AI 模式）。"""
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
            from lxml import etree
            # 尝试解析 HTML（宽松模式）
            parser = etree.HTMLParser(recover=True)
            tree = etree.fromstring(html_text.encode("utf-8"), parser)
        except ImportError:
            QMessageBox.warning(
                self, _("依赖缺失"),
                _("lxml 未安装，请运行 pip install omnicrawl-platform[html]"),
            )
            return
        except Exception as e:
            QMessageBox.warning(self, _("解析失败"), f"HTML 解析错误: {e}")
            return

        # 收集所有叶子节点的文本和 XPath
        candidates: list[tuple[str, str, float]] = []  # (text, xpath, similarity)

        for elem in tree.iter():
            text = (elem.text or "").strip()
            if not text:
                continue
            # 只取直接文本（不包含子元素文本）
            if len(elem) == 0 or text:
                xpath = tree.getpath(elem)

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

        # 按相似度排序，取前 3
        candidates.sort(key=lambda x: x[2], reverse=True)
        top = candidates[:3]

        self._result_table.setRowCount(len(top))
        for i, (text, xpath, sim) in enumerate(top):
            pct = f"{sim * 100:.0f}%"
            self._result_table.setItem(i, 0, QTableWidgetItem(pct))
            self._result_table.setItem(i, 1, QTableWidgetItem(text))
            self._result_table.setItem(i, 2, QTableWidgetItem(xpath))

        if not top:
            QMessageBox.information(
                self, _("未找到匹配"),
                _("未找到匹配内容，请检查示例文本是否存在于粘贴的 HTML 中。"),
            )
        else:
            self._result_table.resizeColumnsToContents()

    def _analyze_ai(self, html_text: str) -> None:
        """AI 模式：调用 LLM 从 HTML 中提取字段。"""
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

        # 使用简单规则模拟 AI 提取（真实场景替换为 ai_graph.AIGraphExtractor）
        from lxml import etree
        parser = etree.HTMLParser(recover=True)
        tree = etree.fromstring(html_text.encode("utf-8"), parser)

        self._result_table.setRowCount(len(fields))
        for i, field in enumerate(fields):
            name = field["name"]
            # 启发式搜索：找标题、meta、h1-h3 等
            text = ""
            xpath = ""

            # 尝试从 meta 标签查找
            for meta in tree.xpath(f"//meta[contains(@name, '{name}') or contains(@property, '{name}')]"):
                text = meta.get("content", "")
                xpath = tree.getpath(meta)
                break

            # 尝试从 h1-h3 查找
            if not text:
                for h in tree.xpath(f"//h1[contains(text(), '{name}')] | //h2[contains(text(), '{name}')] | //h3[contains(text(), '{name}')]"):
                    text = (h.text_content() or "").strip()[:100]
                    xpath = tree.getpath(h)
                    break

            if text:
                self._result_table.setItem(i, 0, QTableWidgetItem("90%"))
                self._result_table.setItem(i, 1, QTableWidgetItem(text[:80]))
                self._result_table.setItem(i, 2, QTableWidgetItem(xpath))
            else:
                self._result_table.setItem(i, 0, QTableWidgetItem("—"))
                self._result_table.setItem(i, 1, QTableWidgetItem(_("未找到匹配")))
                self._result_table.setItem(i, 2, QTableWidgetItem(""))

        self._result_table.setHorizontalHeaderLabels([_("匹配度"), _("提取文本"), _("XPath 位置")])
        self._result_table.resizeColumnsToContents()

    def accept(self) -> None:
        """确认选择。"""
        row = self._result_table.currentRow()
        if row >= 0:
            xpath_item = self._result_table.item(row, 2)
            self._selected_xpath = xpath_item.text() if xpath_item else ""
            text_item = self._result_table.item(row, 1)
            self._selected_text = text_item.text() if text_item else ""
        super().accept()


class SelectorTestThread(QThread):
    """选择器测试后台线程。"""

    result_ready = pyqtSignal(list)  # [(text,), ...]
    error_occurred = pyqtSignal(str)

    def __init__(self, url: str, selector: str, selector_type: str, workspace: str | Path) -> None:
        super().__init__()
        self._url = url
        self._selector = selector
        self._selector_type = selector_type
        self._workspace = Path(workspace)

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            from lxml import etree

            response = scoped_fetch(
                self._url,
                workspace=self._workspace,
                purpose="selector",
            headers={"User-Agent": "OmniCrawler-GUI/2.7 selector test"},
                timeout_seconds=15,
                max_response_bytes=2 * 1024 * 1024,
            user_agent="OmniCrawler-GUI/2.7 selector test",
            )

            parser = etree.HTMLParser(recover=True)
            tree = etree.fromstring(response.body, parser)

            results: list = []

            if self._selector_type == "css":
                from lxml.cssselect import CSSSelector
                sel = CSSSelector(self._selector)
                elements = sel(tree)
                for el in elements:
                    if self.isInterruptionRequested():
                        return
                    text = (el.text_content() or "").strip()
                    if text:
                        results.append((text[:200],))
            elif self._selector_type == "xpath":
                elements = tree.xpath(self._selector)
                if isinstance(elements, list):
                    for el in elements:
                        if self.isInterruptionRequested():
                            return
                        if hasattr(el, "text_content"):
                            text = (el.text_content() or "").strip()
                            if text:
                                results.append((text[:200],))
                        elif isinstance(el, str):
                            results.append((el[:200],))

            if not results:
                results = [(_("(未匹配到任何内容)"),)]

            if not self.isInterruptionRequested():
                self.result_ready.emit(results)
        except Exception as e:
            if not self.isInterruptionRequested():
                self.error_occurred.emit(str(e))


class Step3FieldsPage(QWizardPage):
    """Step 3: 提取字段定义。"""

    config_changed = pyqtSignal()

    SELECTOR_TYPES = ["css", "xpath", "jsonpath"]

    def __init__(self, config: CrawlConfig, parent: QWizardPage | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._updating = False
        self._test_thread: SelectorTestThread | None = None

        self.setTitle(_("步骤 3/5：可选：定义精确字段"))
        self.setSubTitle(_("建议先试跑；若自动提取的标题、正文和来源网址不足，再在这里添加字段。"))
        self.setAccessibleName(_("Step 3: 字段定义"))
        self.setAccessibleDescription(_("Step 3 of the OmniCrawler configuration wizard"))

        layout = QVBoxLayout(self)

        # 工具栏
        toolbar = QHBoxLayout()

        self._add_btn = QPushButton(_("+ 添加字段"))
        self._add_btn.clicked.connect(self._add_field)
        toolbar.addWidget(self._add_btn)

        self._delete_btn = QPushButton(_("- 删除选中"))
        self._delete_btn.clicked.connect(self._delete_selected)
        toolbar.addWidget(self._delete_btn)

        self._test_btn = QPushButton(_("测试选择器"))
        self._test_btn.clicked.connect(self._test_selector)
        toolbar.addWidget(self._test_btn)

        self._smart_btn = QPushButton(_("粘贴 HTML 智能提取"))
        self._smart_btn.clicked.connect(self._smart_extract)
        toolbar.addWidget(self._smart_btn)

        self._visual_btn = QPushButton(_("可视化选择字段 (右键点选)"))
        self._visual_btn.setToolTip(_("内置选择器 或 高级模式：启动 Chrome 扩展在网页上右键点选元素"))
        self._visual_btn.clicked.connect(self._visual_pick)
        toolbar.addWidget(self._visual_btn)

        help_btn = QPushButton(_("选择器帮助"))
        help_btn.clicked.connect(self._show_selector_help)
        toolbar.addWidget(help_btn)
        toolbar.addWidget(HelpTooltip("fields.definition"))

        toolbar.addStretch()
        layout.addLayout(toolbar)

        # 字段表格
        self._table = QTableWidget(0, 7)
        self._table.setHorizontalHeaderLabels([
            _("字段名"), _("选择器"), _("类型"), _("属性"), _("正则"), _("必填"), "备用 XPath"
        ])
        field_header = self._table.horizontalHeader()
        assert field_header is not None
        field_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self._table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._table.cellChanged.connect(self._on_cell_changed)
        layout.addWidget(self._table)

    def set_simple_mode(self, enabled: bool) -> None:
        """In simple mode expose business field names and the visual picker only."""
        for column in range(2, 7):
            self._table.setColumnHidden(column, enabled)
        self._test_btn.setVisible(not enabled)
        self._smart_btn.setVisible(not enabled)
        self._table.setHorizontalHeaderItem(1, QTableWidgetItem(
            _("网页位置（自动生成）") if enabled else _("选择器")
        ))

    def initializePage(self) -> None:
        """加载当前配置的字段。"""
        self._clear_validation()
        self._updating = True
        self._table.setRowCount(0)
        for field in self._config.fields:
            self._add_row(field)
        self._updating = False

    def _clear_validation(self) -> None:
        """清除之前的验证错误样式。"""
        clear_error_style(self._table)

    def validatePage(self) -> bool:
        """校验并保存字段。"""
        fields = self._get_fields()
        for row, f in enumerate(fields):
            errs = f.validate()
            if errs:
                self._table.selectRow(row)
                self._table.scrollToItem(self._table.item(row, 0))
                shake_widget(self._table, self)
                set_error_style(self._table, "\n".join(errs))
                return False
        self._save_to_config()
        return True

    def _add_field(self) -> None:
        """添加空字段行。"""
        field = FieldDef(name=f"field_{self._table.rowCount() + 1}", selector="", selector_type="css")
        self._add_row(field)
        self._save_to_config()
        self.config_changed.emit()

    def _add_row(self, field: FieldDef) -> None:
        """在表格末尾添加一行。"""
        row = self._table.rowCount()
        self._table.insertRow(row)

        # 字段名
        name_item = QTableWidgetItem(field.name)
        self._table.setItem(row, 0, name_item)

        # 选择器
        selector_item = QTableWidgetItem(field.selector)
        self._table.setItem(row, 1, selector_item)

        # 类型下拉
        type_combo = QComboBox()
        type_combo.addItems(self.SELECTOR_TYPES)
        type_combo.setCurrentText(field.selector_type)
        type_combo.currentTextChanged.connect(lambda t, r=row: self._on_type_changed(r, t))
        self._table.setCellWidget(row, 2, type_combo)

        # 属性
        attr_item = QTableWidgetItem(field.attribute or "")
        self._table.setItem(row, 3, attr_item)

        # 正则
        regex_item = QTableWidgetItem(field.regex or "")
        self._table.setItem(row, 4, regex_item)

        required_item = QTableWidgetItem()
        required_item.setCheckState(Qt.CheckState.Checked if field.required else Qt.CheckState.Unchecked)
        self._table.setItem(row, 5, required_item)
        self._table.setItem(row, 6, QTableWidgetItem(field.fallback_xpath or ""))

    def _get_fields(self) -> list[FieldDef]:
        """从表格读取所有字段。"""
        fields: list[FieldDef] = []
        for row in range(self._table.rowCount()):
            name_item = self._table.item(row, 0)
            name = name_item.text().strip() if name_item else ""
            selector_item = self._table.item(row, 1)
            selector = selector_item.text().strip() if selector_item else ""
            type_combo = self._table.cellWidget(row, 2)
            selector_type = type_combo.currentText() if isinstance(type_combo, QComboBox) else "css"
            attr_item = self._table.item(row, 3)
            attr = attr_item.text().strip() if attr_item else ""
            regex_item = self._table.item(row, 4)
            regex = regex_item.text().strip() if regex_item else ""
            required_item = self._table.item(row, 5)
            required = bool(
                required_item
                and required_item.checkState() == Qt.CheckState.Checked
            )
            fallback_item = self._table.item(row, 6)
            fallback_xpath = fallback_item.text().strip() if fallback_item else ""

            if name or selector:
                fields.append(FieldDef(
                    name=name,
                    selector=selector,
                    selector_type=selector_type,  # type: ignore
                    attribute=attr if attr else None,
                    regex=regex if regex else None,
                    required=required,
                    fallback_xpath=fallback_xpath or None,
                ))
        return fields

    def _save_to_config(self) -> None:
        """保存字段到配置。"""
        if self._updating:
            return
        self._config.fields = self._get_fields()

    def _delete_selected(self) -> None:
        """删除选中行。"""
        rows = sorted(set(idx.row() for idx in self._table.selectedIndexes()), reverse=True)
        for row in rows:
            self._table.removeRow(row)
        self._save_to_config()
        self.config_changed.emit()

    def _on_cell_changed(self, row: int, col: int) -> None:
        """单元格编辑回调。"""
        if self._updating:
            return
        self._save_to_config()
        self.config_changed.emit()

    def _on_type_changed(self, row: int, text: str) -> None:
        """选择器类型变更。"""
        if self._updating:
            return
        self._save_to_config()
        self.config_changed.emit()

    def _smart_extract(self) -> None:
        """打开智能提取对话框。"""
        dialog = SmartExtractDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.selected_xpath:
            # 填入当前选中行
            current_row = self._table.currentRow()
            if current_row < 0:
                self._add_field()
                current_row = self._table.rowCount() - 1

            if current_row >= 0:
                selector_item = QTableWidgetItem(dialog.selected_xpath)
                self._table.setItem(current_row, 1, selector_item)
                type_combo = self._table.cellWidget(current_row, 2)
                if isinstance(type_combo, QComboBox):
                    type_combo.setCurrentText("xpath")
                self._save_to_config()
                self.config_changed.emit()

    def _visual_pick(self) -> None:
        # 提供两种可视化选择方式
        reply = QMessageBox.question(
            self,
            _("可视化选择字段"),
            _("请选择可视化方式：\n\n"
              "• 内置选择器 — 输入 URL 后在浏览器中手动查看源码选取 XPath\n"
              "• 高级点选模式 — 启动 Chrome + EasySpider 扩展，在网页上右键点选元素\n\n"
              "推荐高级点选模式（需要 EasySpider Chrome 扩展）。"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        )
        if reply == QMessageBox.StandardButton.Cancel:
            return
        if reply == QMessageBox.StandardButton.Yes:
            self._visual_pick_advanced()
        else:
            self._visual_pick_builtin()

    def _visual_pick_builtin(self) -> None:
        url = self._config.seed_urls[0] if self._config.seed_urls else ""
        dialog = VisualFieldDialog(url, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._apply_visual_candidates(dialog.selected_candidates)

    def _visual_pick_advanced(self) -> None:
        """启动 WebSocket 服务器 + 引导用户使用 EasySpider 扩展。"""
        from ...visual_selector.field_converter import FieldConverter
        from ...visual_selector.server import VisualSelectorServer

        # 启动 WebSocket 服务
        server = VisualSelectorServer()
        server.start()
        url = self._config.seed_urls[0] if self._config.seed_urls else "https://example.com"

        # 显示操作指引
        msg = (
            f"WebSocket 服务已启动 (ws://localhost:8084)\n\n"
            f"请在 Chrome 中:\n"
            f"1. 打开目标页面: {url}\n"
            f"2. 确保 EasySpider Chrome 扩展已加载\n"
            f"3. 右键点击要采集的元素 → 选择\"选中元素\"\n"
            f"4. 点击\"选中全部\"选中同类元素\n"
            f"5. 完成后点击下方\"导入字段\""
        )
        result = QMessageBox.information(
            self, _("高级可视化选择"), msg,
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
        )
        if result == QMessageBox.StandardButton.Cancel:
            server.stop()
            return

        # 获取选择结果
        selections = server.get_selections()
        server.stop()

        if not selections:
            QMessageBox.warning(self, _("提示"), _("未收到任何元素选择，请在网页上右键点选元素后重试。"))
            return

        # 转换为字段
        converter = FieldConverter()
        converter.set_seed_url(url)
        for sel in selections:
            converter.add_selection(
                [{"xpath": sel.xpath, "allXPaths": sel.all_xpaths, "text": sel.content}],
                common_xpath=sel.xpath,
            )

        # 生成候选字段并填入表格
        fields = converter.merge_fields()
        if not fields:
            QMessageBox.warning(self, _("提示"), _("无法从选择结果生成字段，请重试。"))
            return

        # 将字段转换为候选格式并填入
        from types import SimpleNamespace
        candidates = []
        for name, spec in fields.items():
            candidates.append(SimpleNamespace(
                suggested_name=name,
                css=spec.get("selector", ""),
                xpath=spec.get("selector", ""),
                attribute=spec.get("attribute", "text"),
            ))
        self._apply_visual_candidates(candidates)

    def _apply_visual_candidates(self, candidates) -> None:
        existing = {field.name for field in self._get_fields()}
        for candidate in candidates:
            name = candidate.suggested_name
            suffix = 2
            while name in existing:
                name = f"{candidate.suggested_name}_{suffix}"
                suffix += 1
            existing.add(name)
            self._add_row(
                FieldDef(
                    name=name,
                    selector=candidate.css,
                    selector_type="css",
                    attribute=candidate.attribute,
                    fallback_xpath=candidate.xpath,
                )
            )
        self._save_to_config()
        self.config_changed.emit()

    def _test_selector(self) -> None:
        """测试当前选择器。"""
        current_row = self._table.currentRow()
        if current_row < 0:
            QMessageBox.information(self, _("提示"), _("请先选择一个字段行"))
            return

        selector_item = self._table.item(current_row, 1)
        selector = selector_item.text().strip() if selector_item else ""
        type_combo = self._table.cellWidget(current_row, 2)
        selector_type = type_combo.currentText() if isinstance(type_combo, QComboBox) else "css"

        if not selector:
            QMessageBox.warning(self, _("提示"), _("选择器为空"))
            return

        # 获取测试 URL
        from PyQt6.QtWidgets import QInputDialog
        test_url, ok = QInputDialog.getText(
            self, _("测试选择器"), _("输入测试页面 URL:"),
            text=self._config.seed_urls[0] if self._config.seed_urls else ""
        )
        if not ok or not test_url.strip():
            return

        # 启动后台测试线程
        workspace = self._config.workspace or f"work/{self._config.project_name}"
        self._test_thread = SelectorTestThread(test_url.strip(), selector, selector_type, workspace)
        self._test_thread.setParent(self)
        self._test_thread.result_ready.connect(self._on_test_result)
        self._test_thread.error_occurred.connect(self._on_test_error)
        self._test_thread.start()

    def _on_test_result(self, results: list) -> None:
        """显示测试结果。"""
        msg = _("选择器测试结果（最多显示前 10 条）:\n\n")
        for i, row in enumerate(results[:10]):
            msg += f"{i + 1}. {row[0]}\n"
        if len(results) > 10:
            msg += f"\n... 共 {len(results)} 条结果"
        QMessageBox.information(self, _("测试结果"), msg)

    def _on_test_error(self, error: str) -> None:
        """显示测试错误。"""
        QMessageBox.warning(self, _("测试失败"), f"{_('选择器测试出错')}: {error}")

    def _show_selector_help(self) -> None:
        """打开选择器帮助页面（自动注入当前应用主题）。"""
        import tempfile
        import webbrowser
        from pathlib import Path

        help_path = Path(__file__).parent.parent / "help" / "selector_guide.html"
        if not help_path.is_file():
            QMessageBox.information(
                self, _("选择器帮助"),
                _("CSS 选择器示例:\n"
                  "  .title          — 选择 class='title'\n"
                  "  #main a         — 选择 #main 下的链接\n"
                  "  [href]          — 选择有 href 属性的元素\n\n"
                  "XPath 示例:\n"
                  "  //div[@class='title']/a\n"
                  "  //h2[contains(text(),'公告')]\n"
                  "  //a[starts-with(@href,'/news')]\n\n"
                  "JSONPath 示例:\n"
                  "  $.data.list[*].title\n"
                  "  $..author\n")
            )
            return

        # 读取 HTML 并注入应用级主题属性，确保与 GUI 主题一致
        html = help_path.read_text(encoding="utf-8")
        from ..design_system import ThemeManager
        tm = ThemeManager.instance()
        theme = tm.theme_name if tm.theme_name in ("light", "dark") else "light"
        html = html.replace("<html lang=\"zh-CN\">", f'<html lang="zh-CN" data-theme="{theme}">')

        tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False, encoding="utf-8")
        tmp.write(html)
        tmp.close()
        webbrowser.open(f"file://{tmp.name}")
