"""The task-first entry page for the configuration wizard.

The first page is the only place a new user needs to make task decisions.  It
starts with a natural-language request, then exposes the small set of choices
that materially affect scope and output.  Later pages are for optional review
and expert refinement rather than collecting missing prerequisites.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    QWizardPage,
)

from ...services.natural_language_task import compile_natural_language
from ...services.ux_service import QuickTaskDraft
from ..core.config_model import CrawlConfig
from ..i18n import _
from ..widgets.form_feedback import clear_error_style, set_error_style, shake_widget
from ..widgets.help_tooltip import HelpTooltip


class Step1SourcePage(QWizardPage):
    """Collect a complete user-facing task brief before any advanced page."""

    config_changed = pyqtSignal()

    SOURCE_KINDS = [
        ("static_html", _("自动识别（推荐）"), _("先快速抓取，必要时自动切换浏览器")),
        ("crawl", _("栏目与链接发现"), _("发现栏目中的详情页并限制在入口站点范围内")),
        ("browser", _("动态浏览器"), _("需要 JavaScript 渲染、登录、搜索或点击")),
        ("rest", _("REST API"), _("JSON/XML 数据接口")),
        ("feed", _("RSS/Feed"), _("RSS/Atom 订阅源")),
        ("focused", _("栏目/主题定向采集"), _("优先查找主题内容与附件")),
    ]
    INTENTS = [
        ("save_page", _("保存一个页面"), "static_html"),
        ("collect_section", _("采集整个栏目"), "crawl"),
        ("download_files", _("下载附件 / PDF"), "focused"),
        ("monitor_changes", _("监测页面变化"), "static_html"),
        ("interactive", _("需要登录、搜索、点击或滚动"), "browser"),
        ("api", _("采集 JSON / API 数据"), "rest"),
    ]
    LEGACY_INTENTS = {
        "auto": "save_page",
        "records": "collect_section",
        "documents": "download_files",
        "updates": "monitor_changes",
    }

    def __init__(self, config: CrawlConfig, parent: QWizardPage | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self._updating = False

        self.setTitle(_("步骤 1/5：先描述你想得到什么"))
        self.setSubTitle(_("在这里填写完整任务意图、入口、范围和结果偏好；后续步骤仅用于按需微调。"))
        self.setAccessibleName(_("Step 1: 任务需求与范围"))
        self.setAccessibleDescription(_("The complete user input page of the OmniCrawler configuration wizard"))

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setSpacing(12)
        scroll.setWidget(content)
        outer.addWidget(scroll)

        brief_group = QGroupBox(_("用自然语言描述任务（推荐，从这里开始）"))
        brief_layout = QVBoxLayout(brief_group)
        brief_hint = QLabel(_(
            _("例如：每周监测 https://example.com/news 中“人工智能”相关政策，下载 PDF 并导出 Excel。\n") +

            _("系统只会生成受入口站点限制的安全草案；不会联网、不会保存真实密钥，也不会跳过试跑。")
        ))
        brief_hint.setWordWrap(True)
        brief_hint.setObjectName("muted")
        brief_layout.addWidget(brief_hint)
        self._task_description = QPlainTextEdit()
        self._task_description.setPlaceholderText(_(
            _("我想从哪个网址获取什么内容，范围有多大，需要哪些文件或结果格式？\n") +

            _("示例：采集 https://example.com/notices 的全部公告，排除“失效”，下载 PDF，输出 Excel。")
        ))
        self._task_description.setMinimumHeight(118)
        self._task_description.setMaximumHeight(180)
        self._task_description.setAccessibleName(_("自然语言任务描述"))
        self._task_description.textChanged.connect(self._on_task_description_changed)
        brief_layout.addWidget(self._task_description)
        brief_actions = QHBoxLayout()
        self._apply_brief_btn = QPushButton(_("应用安全建议"))
        self._apply_brief_btn.setProperty("primary", True)
        self._apply_brief_btn.setToolTip(_("从描述中提取网址、采集范围、附件、变化监测和主题词；所有结果仍可编辑。"))
        self._apply_brief_btn.clicked.connect(self._apply_natural_language)
        brief_actions.addWidget(self._apply_brief_btn)
        brief_actions.addWidget(HelpTooltip("task.intent"))
        brief_actions.addStretch()
        brief_layout.addLayout(brief_actions)
        self._brief_feedback = QLabel("")
        self._brief_feedback.setWordWrap(True)
        self._brief_feedback.setObjectName("muted")
        self._brief_feedback.setAccessibleName(_("需求解析建议"))
        brief_layout.addWidget(self._brief_feedback)
        layout.addWidget(brief_group)

        essentials = QGroupBox(_("任务要点（可直接修改系统建议）"))
        form = QFormLayout(essentials)
        self._task_name = QLineEdit()
        self._task_name.setPlaceholderText(_("例如：政策栏目—人工智能主题 PDF"))
        self._task_name.setClearButtonEnabled(True)
        self._task_name.textChanged.connect(self._on_data_changed)
        form.addRow(_label_with_help(_("任务名称："), "task.name"), self._task_name)

        url_row = QHBoxLayout()
        self._primary_url = QLineEdit()
        self._primary_url.setPlaceholderText(_("https://example.com/news"))
        self._primary_url.setClearButtonEnabled(True)
        self._primary_url.setAccessibleName(_("任务入口网址"))
        self._primary_url.textChanged.connect(self._on_data_changed)
        url_row.addWidget(self._primary_url, 1)
        paste_btn = QPushButton(_("粘贴"))
        paste_btn.clicked.connect(self._paste_url)
        url_row.addWidget(paste_btn)
        form.addRow(_label_with_help(_("目标网址（必填）："), "source.seed"), url_row)

        self._intent_combo = QComboBox()
        for key, label, _source in self.INTENTS:
            self._intent_combo.addItem(label, key)
        self._intent_combo.currentIndexChanged.connect(self._on_intent_changed)
        form.addRow(_label_with_help(_("我想做什么："), "task.intent"), self._intent_combo)

        self._max_pages = QSpinBox()
        self._max_pages.setRange(1, 100000)
        self._max_pages.setValue(10)
        self._max_pages.setSuffix(_(" 页"))
        self._max_pages.valueChanged.connect(self._on_data_changed)
        form.addRow(_label_with_help(_("最多处理："), "crawl.max_pages"), self._max_pages)

        self._topic_any = QLineEdit()
        self._topic_any.setPlaceholderText(_("可选，逗号分隔；例如：人工智能, 大模型"))
        self._topic_any.textChanged.connect(self._on_data_changed)
        form.addRow(_label_with_help(_("只要包含任一主题词："), "selection.topic"), self._topic_any)
        layout.addWidget(essentials)

        results = QGroupBox(_("文件、变化与结果（默认可直接开始试跑）"))
        self._results_group = results
        results_layout = QVBoxLayout(results)
        self._download_enabled = QCheckBox(_("下载附件（PDF、Office 文件等）"))
        self._download_enabled.toggled.connect(self._on_data_changed)
        results_layout.addWidget(self._download_enabled)
        self._process_pdf = QCheckBox(_("下载后提取 PDF 文本、表格和元数据"))
        self._process_pdf.toggled.connect(self._on_data_changed)
        results_layout.addWidget(self._process_pdf)
        self._monitor_same_url = QCheckBox(_("网址不变时也保留新版本并比较变化"))
        self._monitor_same_url.toggled.connect(self._on_data_changed)
        results_layout.addWidget(self._monitor_same_url)
        self._snapshot_mode = QCheckBox(_("保存完整页面快照（单文件 HTML，含 CSS/图片）"))
        self._snapshot_mode.setToolTip(_("将抓取页面保存为自包含的单文件 HTML，适合离线归档"))
        self._snapshot_mode.toggled.connect(self._on_data_changed)
        results_layout.addWidget(self._snapshot_mode)
        output_row = QHBoxLayout()
        output_row.addWidget(QLabel(_("结果格式：")))
        self._output_checks: dict[str, QCheckBox] = {}
        for key, label in (("jsonl", "JSONL"), ("csv", "CSV"), ("xlsx", "Excel"), ("parquet", "Parquet"), ("duckdb", "DuckDB")):
            check = QCheckBox(label)
            check.toggled.connect(self._on_data_changed)
            self._output_checks[key] = check
            output_row.addWidget(check)
        output_row.addStretch()
        results_layout.addLayout(output_row)
        layout.addWidget(results)

        advanced = QGroupBox(_("技术方式（通常不需要修改）"))
        advanced_form = QFormLayout(advanced)
        self._kind_combo = QComboBox()
        for key, name, desc in self.SOURCE_KINDS:
            self._kind_combo.addItem(f"{name}  -  {desc}", key)
        self._kind_combo.currentIndexChanged.connect(self._on_kind_changed)
        advanced_form.addRow(_label_with_help(_("获取方式："), "source.kind"), self._kind_combo)
        self._desc_label = QLabel()
        self._desc_label.setWordWrap(True)
        self._desc_label.setObjectName("muted")
        advanced_form.addRow(self._desc_label)
        layout.addWidget(advanced)
        layout.addStretch()

    def initializePage(self) -> None:
        """Load the complete task brief without emitting stale cross-page updates."""
        self._clear_validation()
        self._updating = True
        self._task_name.setText(self._config.project_name)
        self._task_description.setPlainText(self._config.task_description)
        self._primary_url.setText(self._config.seed_urls[0] if self._config.seed_urls else "")
        self._max_pages.setValue(self._config.max_pages)
        self._topic_any.setText(", ".join(self._config.topic_include_any))
        self._download_enabled.setChecked(self._config.download.enabled)
        self._process_pdf.setChecked(self._config.process_pdf)
        self._monitor_same_url.setChecked(self._config.monitor_same_url)
        self._snapshot_mode.setChecked(self._config.snapshot_mode)
        for key, check in self._output_checks.items():
            check.setChecked(key in self._config.output_formats)
        self._set_combo_data(self._intent_combo, self._canonical_intent(self._config.task_intent))
        if not self._set_combo_data(self._kind_combo, self._config.source_kind):
            self._kind_combo.addItem(
                _("模板专用来源（保持原配置）") + f" - {self._config.source_kind}",
                self._config.source_kind,
            )
            self._kind_combo.setCurrentIndex(self._kind_combo.count() - 1)
        self._update_description()
        self._update_enabled_state()
        self._updating = False

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str) -> bool:
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return True
        return False

    def _canonical_intent(self, value: str) -> str:
        value = str(value or "save_page")
        return self.LEGACY_INTENTS.get(value, value if any(item[0] == value for item in self.INTENTS) else "save_page")

    def _clear_validation(self) -> None:
        clear_error_style(self._primary_url)
        clear_error_style(self._task_description)
        clear_error_style(self._results_group)

    def validatePage(self) -> bool:
        url = self._normalized_url()
        self._clear_validation()
        if not self._is_supported_url(url):
            shake_widget(self._primary_url, self)
            set_error_style(self._primary_url, _("请输入完整的 http(s) 地址或本地 file 地址"))
            self._primary_url.setFocus()
            return False
        if not any(check.isChecked() for check in self._output_checks.values()):
            shake_widget(self._results_group, self)
            set_error_style(self._results_group, _("请至少选择一种结果格式"))
            self._brief_feedback.setText(_("请在“文件、变化与结果”中至少选择一种结果格式。"))
            return False
        self._primary_url.setText(url)
        self._save_to_config()
        return True

    def focus_primary_url(self) -> None:
        self._primary_url.setFocus()
        self._primary_url.selectAll()

    def _normalized_url(self) -> str:
        value = self._primary_url.text().strip()
        return "https://" + value if value and "://" not in value else value

    @staticmethod
    def _is_supported_url(value: str) -> bool:
        """Validate the local entry before later pages or a run consume it."""
        if any(char.isspace() for char in value):
            return False
        try:
            parsed = urlsplit(value)
        except ValueError:
            return False
        if parsed.scheme in {"http", "https"}:
            return bool(parsed.netloc)
        return parsed.scheme == "file" and bool(parsed.path)

    def _paste_url(self) -> None:
        clipboard = QApplication.clipboard()
        assert clipboard is not None
        text = clipboard.text().strip()
        if text:
            self._primary_url.setText(text.splitlines()[0])
        self.focus_primary_url()

    def _on_task_description_changed(self) -> None:
        if self._updating:
            return
        self._config.task_description = self._task_description.toPlainText().strip()
        self.config_changed.emit()

    def _apply_natural_language(self) -> None:
        request = self._task_description.toPlainText().strip()
        if not request:
            shake_widget(self._task_description, self)
            set_error_style(self._task_description, _("请先描述要采集的内容、范围或结果"))
            self._task_description.setFocus()
            return
        try:
            draft = compile_natural_language(request, fallback_url=self._normalized_url())
        except ValueError as exc:
            set_error_style(self._task_description, str(exc))
            self._brief_feedback.setText(_("需要补充：{0}").format(exc))
            return
        self._apply_draft(draft.task, list(draft.topics))
        cadence = {"weekly": _("每周"), "daily": _("每天"), "monthly": _("每月"), "manual": _("手动")}
        self._brief_feedback.setText(_(
            _("已应用建议：{intent}，最多 {pages} 页；主题词 {topics}；建议执行频率：{schedule}。") +

            _("仍可在本页直接修改，下一步只处理高级范围。")
        ).format(
            intent=self._intent_combo.currentText(),
            pages=self._config.max_pages,
            topics=_('未指定') if not draft.topics else "、".join(draft.topics),
            schedule=cadence.get(draft.schedule, draft.schedule),
        ))
        clear_error_style(self._task_description)
        self.config_changed.emit()

    def _apply_draft(self, draft: QuickTaskDraft, topics: list[str]) -> None:
        """Apply deterministic suggestions while keeping every result editable."""
        self._config.task_description = self._task_description.toPlainText().strip()
        self._config.seed_urls = [draft.url]
        self._config.task_intent = self._canonical_intent(draft.intent)
        self._config.source_kind = draft.source_kind
        self._config.max_pages = draft.max_pages
        self._config.download.enabled = draft.download_files
        self._config.process_pdf = draft.process_pdf
        self._config.monitor_same_url = draft.monitor_changes
        self._config.incremental = draft.monitor_changes
        self._config.output_formats = list(draft.output_formats)
        if topics:
            self._config.topic_include_any = list(dict.fromkeys(topic for topic in topics if topic.strip()))
        if self._is_generated_name(self._config.project_name):
            self._config.project_name = self._suggest_task_name(draft)
            self._config.workspace = f"work/{self._config.project_name}"
        self.initializePage()

    @staticmethod
    def _is_generated_name(value: str) -> bool:
        return not value.strip() or value.startswith("task_")

    @staticmethod
    def _suggest_task_name(draft: QuickTaskDraft) -> str:
        host = urlsplit(draft.url).hostname or "task"
        labels = {
            "save_page": _("页面采集"),
            "collect_section": _("栏目采集"),
            "download_files": _("附件采集"),
            "monitor_changes": _("变化监测"),
        }
        return _(f"{host}-{labels.get(draft.intent, '采集任务')}")

    def _on_data_changed(self) -> None:
        if self._updating:
            return
        if self._is_supported_url(self._normalized_url()):
            clear_error_style(self._primary_url)
        if any(check.isChecked() for check in self._output_checks.values()):
            clear_error_style(self._results_group)
        self._save_to_config()
        self._update_enabled_state()
        self.config_changed.emit()

    def _on_kind_changed(self) -> None:
        if self._updating:
            return
        self._save_to_config()
        self._update_description()
        self.config_changed.emit()

    def _on_intent_changed(self) -> None:
        if self._updating:
            return
        intent = self._canonical_intent(str(self._intent_combo.currentData() or "save_page"))
        self._config.task_intent = intent
        source = next((value[2] for value in self.INTENTS if value[0] == intent), "static_html")
        self._updating = True
        self._set_combo_data(self._kind_combo, source)
        if intent == "download_files":
            self._download_enabled.setChecked(True)
            self._process_pdf.setChecked(True)
            self._config.download.extensions = [".pdf", ".doc", ".docx", ".xls", ".xlsx", ".zip"]
        elif intent == "monitor_changes":
            self._monitor_same_url.setChecked(True)
        self._updating = False
        self._save_to_config()
        self._update_description()
        self._update_enabled_state()
        self.config_changed.emit()

    def _save_to_config(self) -> None:
        url = self._normalized_url()
        remaining = self._config.seed_urls[1:] if self._config.seed_urls else []
        self._config.seed_urls = ([url] if url else []) + remaining
        name = self._task_name.text().strip()
        if name:
            old_name = self._config.project_name
            self._config.project_name = name
            if not self._config.workspace or self._config.workspace == f"work/{old_name}":
                self._config.workspace = f"work/{name}"
        self._config.task_description = self._task_description.toPlainText().strip()
        self._config.task_intent = self._canonical_intent(str(self._intent_combo.currentData() or "save_page"))
        self._config.source_kind = str(self._kind_combo.currentData() or "static_html")
        self._config.max_pages = self._max_pages.value()
        self._config.topic_include_any = _words(self._topic_any.text())
        self._config.download.enabled = self._download_enabled.isChecked()
        self._config.process_pdf = self._process_pdf.isChecked()
        self._config.monitor_same_url = self._monitor_same_url.isChecked()
        self._config.incremental = self._config.monitor_same_url
        self._config.snapshot_mode = self._snapshot_mode.isChecked()
        self._config.output_formats = [key for key, check in self._output_checks.items() if check.isChecked()]

    def _update_enabled_state(self) -> None:
        enabled = self._download_enabled.isChecked()
        self._process_pdf.setEnabled(enabled)
        if not enabled and self._process_pdf.isChecked() and not self._updating:
            self._process_pdf.setChecked(False)

    def _update_description(self) -> None:
        descriptions = {
            "static_html": _("自动识别：先使用轻量 HTTP；页面确实需要 JavaScript 时才切换浏览器。"),
            "crawl": _("栏目发现：在入口站点内发现链接，受最大页数和同站范围限制。"),
            "browser": _("动态浏览器：用于登录、交互或 JavaScript 页面，速度和资源消耗会更高。"),
            "rest": _("REST API：用于结构化 JSON/XML 接口；请求头和认证可在高级页确认。"),
            "feed": _("RSS/Feed：读取订阅源并适合按计划检查更新。"),
            "focused": _("主题定向采集：优先寻找与主题词及附件相关的栏目内容。"),
        }
        self._desc_label.setText(descriptions.get(str(self._kind_combo.currentData() or ""), ""))


def _label_with_help(text: str, key: str) -> QWidget:
    widget = QWidget()
    row = QHBoxLayout(widget)
    row.setContentsMargins(0, 0, 0, 0)
    row.addWidget(QLabel(text))
    row.addWidget(HelpTooltip(key))
    row.addStretch()
    return widget


def _words(value: str) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in value.replace("，", ",").split(",") if item.strip()))
