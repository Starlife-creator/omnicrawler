"""Step 4: 下载选项配置页面。

设置文件下载开关、扩展名过滤和输出目录。
"""

from __future__ import annotations

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWizardPage,
)

from ..core.config_model import CrawlConfig
from ..i18n import _
from ..widgets.help_tooltip import HelpTooltip


class Step4DownloadPage(QWizardPage):
    """Step 4: 下载选项。"""

    config_changed = pyqtSignal()

    def __init__(self, config: CrawlConfig, parent: QWizardPage | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._updating = False

        self.setTitle(_("步骤 4/5：可选：细化筛选与处理"))
        self.setSubTitle(_("第一页已设置主题、附件、变化和结果格式；仅在需要排除词、OCR、输出路径或 AI 时修改。"))
        self.setAccessibleName(_("Step 4: 下载与输出"))
        self.setAccessibleDescription(_("Step 4 of the OmniCrawler configuration wizard"))

        layout = QVBoxLayout(self)

        # 启用下载
        self._download_enabled = QCheckBox(_("启用自动下载文件"))
        self._download_enabled.toggled.connect(self._on_data_changed)
        layout.addWidget(self._download_enabled)

        # 下载配置
        self._download_group = QGroupBox(_("附件下载"))
        download_form = QFormLayout(self._download_group)

        self._extensions = QLineEdit()
        self._extensions.setPlaceholderText(".pdf, .jpg, .png, .doc")
        self._extensions.textChanged.connect(self._on_data_changed)
        download_form.addRow(_("文件扩展名:"), self._extensions)

        # 输出目录
        self._output_layout = QHBoxLayout()
        self._output_dir = QLineEdit()
        self._output_dir.setPlaceholderText("downloads")
        self._output_dir.textChanged.connect(self._on_data_changed)
        self._output_layout.addWidget(self._output_dir)

        self._browse_btn = QPushButton(_("浏览..."))
        self._browse_btn.clicked.connect(self._browse_output)
        self._output_layout.addWidget(self._browse_btn)

        download_form.addRow(_("输出目录:"), self._output_layout)

        download_form.addRow(HelpTooltip("download.files"))
        layout.addWidget(self._download_group)

        topic_group = QGroupBox(_("栏目/主题筛选（可选）"))
        topic_form = QFormLayout(topic_group)
        self._topic_any = QLineEdit()
        self._topic_any.setPlaceholderText(_("任一命中即可，例如：人工智能, 大模型"))
        self._topic_any.textChanged.connect(self._on_data_changed)
        topic_form.addRow(_("主题词:"), self._topic_any)
        self._topic_all = QLineEdit()
        self._topic_all.setPlaceholderText(_("必须全部出现，可留空"))
        self._topic_all.textChanged.connect(self._on_data_changed)
        topic_form.addRow(_("必含词:"), self._topic_all)
        self._topic_exclude = QLineEdit()
        self._topic_exclude.setPlaceholderText(_("例如：征求意见稿, 失效"))
        self._topic_exclude.textChanged.connect(self._on_data_changed)
        topic_form.addRow(_("排除词:"), self._topic_exclude)
        self._keep_uncertain = QCheckBox(_("保留链接阶段无法判断的候选，读取正文后再确认（推荐）"))
        self._keep_uncertain.toggled.connect(self._on_data_changed)
        topic_form.addRow(self._keep_uncertain)
        topic_form.addRow(HelpTooltip("selection.topic"))
        layout.addWidget(topic_group)

        process_group = QGroupBox(_("PDF 与同址变化"))
        process_form = QFormLayout(process_group)
        self._process_pdf = QCheckBox(_("下载后提取 PDF 文本、表格和元数据"))
        self._process_pdf.toggled.connect(self._on_data_changed)
        process_form.addRow(self._process_pdf, HelpTooltip("processors.pdf"))
        self._ocr = QComboBox()
        self._ocr.addItem(_("自动：仅扫描件使用 OCR（推荐）"), "auto")
        self._ocr.addItem(_("不使用 OCR"), "never")
        self._ocr.addItem("PaddleOCR", "paddle")
        self._ocr.addItem("Tesseract", "tesseract")
        self._ocr.currentIndexChanged.connect(self._on_data_changed)
        process_form.addRow(_("OCR:"), self._ocr)
        self._monitor_same_url = QCheckBox(_("网址不变也重新访问并保存变化版本"))
        self._monitor_same_url.toggled.connect(self._on_data_changed)
        process_form.addRow(self._monitor_same_url, HelpTooltip("updates.same_url"))
        layout.addWidget(process_group)

        output_group = QGroupBox(_("结果格式"))
        output_row = QHBoxLayout(output_group)
        self._output_checks: dict[str, QCheckBox] = {}
        for key, label in (("jsonl", "JSONL"), ("csv", "CSV"), ("xlsx", "Excel"), ("parquet", "Parquet"), ("duckdb", "DuckDB")):
            check = QCheckBox(label)
            check.toggled.connect(self._on_data_changed)
            self._output_checks[key] = check
            output_row.addWidget(check)
        output_row.addWidget(HelpTooltip("outputs.formats"))
        output_row.addStretch()
        layout.addWidget(output_group)

        self._ai_group = QGroupBox(_("AI 增强（完全可选）"))
        ai_form = QFormLayout(self._ai_group)
        self._ai_mode = QComboBox()
        self._ai_mode.addItem(_("关闭（默认）"), "disabled")
        self._ai_mode.addItem(_("本地模型 / Ollama 等"), "local")
        self._ai_mode.addItem(_("云端 OpenAI 兼容 API"), "cloud")
        self._ai_mode.addItem(_("自定义 OpenAI 兼容 API"), "custom")
        self._ai_mode.currentIndexChanged.connect(self._on_data_changed)
        ai_form.addRow(_("模式:"), self._ai_mode)
        self._ai_provider = QLineEdit()
        self._ai_provider.setPlaceholderText("my_provider")
        self._ai_provider.textChanged.connect(self._on_data_changed)
        ai_form.addRow(_("服务名称:"), self._ai_provider)
        self._ai_base_url = QLineEdit()
        self._ai_base_url.setPlaceholderText("https://api.example.com/v1")
        self._ai_base_url.textChanged.connect(self._on_data_changed)
        ai_form.addRow(_("API 地址:"), self._ai_base_url)
        self._ai_model = QLineEdit()
        self._ai_model.setPlaceholderText(_("服务提供的模型 ID"))
        self._ai_model.textChanged.connect(self._on_data_changed)
        ai_form.addRow(_("模型:"), self._ai_model)
        self._ai_key_ref = QLineEdit()
        self._ai_key_ref.setPlaceholderText("secret://env/OMNICRAWL_AI_KEY")
        self._ai_key_ref.textChanged.connect(self._on_data_changed)
        ai_form.addRow(_("密钥引用:"), self._ai_key_ref)
        ai_form.addRow(HelpTooltip("ai.mode"))
        layout.addWidget(self._ai_group)

        # 提示
        hint = QLabel(_("提示: 扩展名用逗号分隔，如 \".pdf,.jpg\"。输出目录相对于项目根目录。"))
        hint.setObjectName("muted")
        layout.addWidget(hint)

        layout.addStretch()

    def initializePage(self) -> None:
        """加载当前配置。"""
        self._updating = True
        self._download_enabled.setChecked(self._config.download.enabled)
        self._extensions.setText(", ".join(self._config.download.extensions))
        self._output_dir.setText(self._config.download.output_dir)
        self._topic_any.setText(", ".join(self._config.topic_include_any))
        self._topic_all.setText(", ".join(self._config.topic_include_all))
        self._topic_exclude.setText(", ".join(self._config.topic_exclude))
        self._keep_uncertain.setChecked(self._config.keep_uncertain_topics)
        self._process_pdf.setChecked(self._config.process_pdf)
        for index in range(self._ocr.count()):
            if self._ocr.itemData(index) == self._config.pdf_ocr:
                self._ocr.setCurrentIndex(index)
                break
        self._monitor_same_url.setChecked(self._config.monitor_same_url)
        for key, check in self._output_checks.items():
            check.setChecked(key in self._config.output_formats)
        for index in range(self._ai_mode.count()):
            if self._ai_mode.itemData(index) == self._config.ai_mode:
                self._ai_mode.setCurrentIndex(index)
                break
        self._ai_provider.setText(self._config.ai_provider)
        self._ai_base_url.setText(self._config.ai_base_url)
        self._ai_model.setText(self._config.ai_model)
        self._ai_key_ref.setText(self._config.ai_api_key_ref)
        self._update_enabled_state()
        self._updating = False

    def validatePage(self) -> bool:
        """校验并保存。"""
        self._save_to_config()
        return True

    def _save_to_config(self) -> None:
        """保存到配置。"""
        self._config.download.enabled = self._download_enabled.isChecked()
        ext_text = self._extensions.text().strip()
        if ext_text:
            self._config.download.extensions = [
                e.strip() for e in ext_text.split(",") if e.strip()
            ]
        self._config.download.output_dir = self._output_dir.text().strip() or "downloads"
        self._config.topic_include_any = _words(self._topic_any.text())
        self._config.topic_include_all = _words(self._topic_all.text())
        self._config.topic_exclude = _words(self._topic_exclude.text())
        self._config.keep_uncertain_topics = self._keep_uncertain.isChecked()
        self._config.process_pdf = self._process_pdf.isChecked()
        self._config.pdf_ocr = str(self._ocr.currentData() or "auto")
        self._config.monitor_same_url = self._monitor_same_url.isChecked()
        if self._config.monitor_same_url:
            self._config.incremental = True
        elif self._config.task_intent not in {"updates", "monitor_changes"}:
            self._config.incremental = False
        self._config.output_formats = [key for key, check in self._output_checks.items() if check.isChecked()]
        self._config.ai_mode = str(self._ai_mode.currentData() or "disabled")
        self._config.ai_provider = self._ai_provider.text().strip()
        self._config.ai_base_url = self._ai_base_url.text().strip()
        self._config.ai_model = self._ai_model.text().strip()
        self._config.ai_api_key_ref = self._ai_key_ref.text().strip()

    def _browse_output(self) -> None:
        """浏览输出目录。"""
        directory = QFileDialog.getExistingDirectory(
            self, _("选择下载输出目录"), self._output_dir.text()
        )
        if directory:
            self._output_dir.setText(directory)
            self._save_to_config()
            self.config_changed.emit()

    def _update_enabled_state(self) -> None:
        """根据复选框状态更新控件。"""
        enabled = self._download_enabled.isChecked()
        self._extensions.setEnabled(enabled)
        self._output_dir.setEnabled(enabled)
        pdf_enabled = enabled and ".pdf" in {item.casefold() for item in _words(self._extensions.text())}
        self._process_pdf.setEnabled(pdf_enabled)
        self._ocr.setEnabled(pdf_enabled and self._process_pdf.isChecked())
        ai_enabled = str(self._ai_mode.currentData() or "disabled") != "disabled"
        for widget in (self._ai_provider, self._ai_base_url, self._ai_model, self._ai_key_ref):
            widget.setEnabled(ai_enabled)

    def set_simple_mode(self, enabled: bool) -> None:
        self._ai_group.setVisible(not enabled or self._config.ai_mode != "disabled")
        self._output_dir.setVisible(not enabled)
        self._browse_btn.setVisible(not enabled)
        form = self._download_group.layout()
        if isinstance(form, QFormLayout):
            label = form.labelForField(self._output_layout)
            if label:
                label.setVisible(not enabled)

    def _on_data_changed(self) -> None:
        """数据变更处理。"""
        if self._updating:
            return
        self._update_enabled_state()
        self._save_to_config()
        self.config_changed.emit()


def _words(value: str) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in value.replace("，", ",").split(",") if item.strip()))

