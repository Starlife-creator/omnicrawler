"""AI 服务中心对话框。

支持：
- OpenAI / OpenAI兼容服务 / Ollama本地模型 / 自定义API
- 模型名称、API Key、安全保存到 Windows 凭据管理器
- 测试连接、获取模型列表
- 超时和响应长度、Token/费用限制
- 内容发送控制（网页正文、PDF、截图）
- 连接状态和错误诊断
- 多 Provider 和按能力路由
"""

from __future__ import annotations

from pathlib import Path
from threading import Event
from typing import Any

from PySide6.QtCore import QStandardPaths, QThread, Signal
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...services.ai_diagnostics import discover, estimate, failure_message, test_generation
from ..i18n import _


class AITestWorker(QThread):
    """测试 AI 连接（不在 UI 线程阻塞）。"""
    test_done = Signal(bool, str)

    def __init__(self, base_url: str, api_key: str, model: str, timeout: int, workspace: Path,
                 *, generate_options: dict[str, Any] | None = None, allow_private: bool = False) -> None:
        super().__init__()
        self._base_url = base_url
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._workspace = workspace
        self._generate_options = generate_options
        self._allow_private = allow_private
        self._cancel = Event()

    def requestInterruption(self) -> None:  # noqa: N802
        self._cancel.set()
        super().requestInterruption()

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            if self._generate_options is None:
                models = discover(self._base_url, self._api_key, self._workspace, timeout=self._timeout, allow_private=self._allow_private)
                message = _("连接可达；所选模型在列表中。生成与结构化输出尚未验证。") if self._model in models else _("连接可达，但所选模型未列出；请核对名称或单独验收生成接口。")
            else:
                result = test_generation(self._base_url, self._api_key, self._model, self._workspace,
                                         timeout=self._timeout, allow_private=self._allow_private,
                                         cancel_event=self._cancel, **self._generate_options)
                accounting = result["accounting"]
                cost = accounting.get("estimated_cost")
                charge = str(cost) if cost is not None else _("待核对")
                tokens = result.get("total_tokens")
                usage = str(tokens) + " Token" if tokens is not None else _("用量未返回")
                message = _("生成验收通过：{0}；{1} 次请求，{2}。\n按所填单价估算费用：{3}；账单仍需到服务商核对。").format(
                    self._model, accounting.get("network_attempts", 1), usage, charge)
                if result["structured_output_tested"]:
                    message += "\n" + _("固定 JSON Schema 样例校验通过。")
            if not self.isInterruptionRequested():
                self.test_done.emit(True, message)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.test_done.emit(False, failure_message(exc))


class AIListModelsWorker(QThread):
    """获取可用模型列表。"""
    models_ready = Signal(list, str)

    def __init__(self, base_url: str, api_key: str, timeout: int, workspace: Path, *, allow_private: bool = False) -> None:
        super().__init__()
        self._base_url = base_url
        self._api_key = api_key
        self._timeout = timeout
        self._workspace = workspace
        self._allow_private = allow_private

    def run(self) -> None:
        try:
            if self.isInterruptionRequested():
                return
            models = discover(self._base_url, self._api_key, self._workspace, timeout=self._timeout, allow_private=self._allow_private)
            if not self.isInterruptionRequested():
                self.models_ready.emit(models, "")
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.models_ready.emit([], failure_message(exc))


class AIServiceCenterDialog(QDialog):
    """全球 AI 服务中心设置对话框。"""

    def __init__(
        self,
        ai_config: dict[str, Any],
        parent: QWidget | None = None,
        *,
        workspace: str | Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._active_workers: list[QThread] = []
        self._close_pending = False
        self.setAccessibleName(_("AI 服务设置"))
        self._ai_config = ai_config
        self._workspace = Path(workspace).expanduser().resolve() if workspace else None
        self.setWindowTitle(_("AI 服务中心"))
        self.setMinimumSize(700, 520)
        self.resize(760, 580)

        layout = QVBoxLayout(self)

        # Tab 结构
        tabs = QTabWidget(self)

        # Tab 1: Provider 配置
        provider_tab = self._build_provider_tab()
        tabs.addTab(provider_tab, _("Provider 配置"))

        # Tab 2: 安全与隐私
        privacy_tab = self._build_privacy_tab()
        tabs.addTab(privacy_tab, _("安全与隐私"))

        # Tab 3: 路由与预算
        routing_tab = self._build_routing_tab()
        tabs.addTab(routing_tab, _("路由与预算"))

        # Tab 4: 智能提取
        extraction_tab = self._build_extraction_tab()
        tabs.addTab(extraction_tab, _("智能提取"))

        layout.addWidget(tabs)

        # 底部按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        assert save_button is not None
        save_button.setText(_("保存设置"))
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # 回填已保存配置（关键：此前遗漏了 api_key 等字段的回填，
        # 导致保存后重开对话框显示为空，用户误以为密钥未持久化）
        self._populate_from_config()

    def _populate_from_config(self) -> None:
        """将已保存的 ai_config 回填到各输入控件。

        修复：旧实现只在切换 Provider 类型时填入默认值，从未从 ai_config
        回填真实保存值，导致重开对话框后 api_key / base_url / model 等字段
        显示为空（尽管 .env 中已正确保存）。
        """
        provider = self._ai_config.get("providers", {}).get("default", {})

        # 先设 Provider 类型，触发 _on_provider_changed 调整启用状态与默认值
        provider_type = provider.get("type", "disabled")
        idx = self._provider_type.findData(provider_type)
        self._provider_type.setCurrentIndex(idx if idx >= 0 else 0)

        # 用真实保存值覆盖默认填充（顺序必须在此之后，确保保存值胜出）
        self._base_url.setText(provider.get("base_url", ""))
        self._model_name.setText(provider.get("model", ""))
        self._api_key.setText(provider.get("api_key", ""))
        if provider.get("timeout_seconds"):
            self._timeout.setValue(int(provider["timeout_seconds"]))

        # 安全与隐私 / 预算
        privacy = self._ai_config.get("privacy", {})
        self._allow_page_text.setChecked(bool(privacy.get("allow_page_text", False)))
        self._allow_pdf_content.setChecked(bool(privacy.get("allow_pdf_content", False)))
        self._allow_screenshots.setChecked(bool(privacy.get("allow_screenshots", False)))
        self._allow_cookies.setChecked(bool(privacy.get("allow_cookies", False)))
        budget = self._ai_config.get("budget", {})
        self._cost_limit.setValue(float(budget.get("maximum_cost", budget.get("max_cost", 0))))
        pricing = provider.get("pricing", {})
        self._input_rate.setValue(float(pricing.get("input_per_million", 0)))
        self._output_rate.setValue(float(pricing.get("output_per_million", 0)))
        self._pricing_confirmed.setChecked("input_per_million" in pricing and "output_per_million" in pricing)
        self._allow_private_endpoint.setChecked(bool(provider.get("diagnostics_allow_private", False)))
        # B4：回填「最大响应长度」(max_tokens_per_request)，否则重开配置丢失
        if budget.get("max_tokens_per_request") is not None:
            self._max_tokens.setValue(int(budget["max_tokens_per_request"]))
        self._log_ai_calls.setChecked(bool(budget.get("log_calls", True)))

        # 能力路由
        routing = self._ai_config.get("routing", {})
        self._route_field_design.setEditText(str(routing.get("field_designer", "")))
        self._route_content_analysis.setEditText(str(routing.get("content_analysis", "")))
        self._route_task_planning.setEditText(str(routing.get("task_planning", "")))
        self._route_pdf_ocr.setEditText(str(routing.get("pdf_ocr", "")))

        # 智能提取
        extraction = self._ai_config.get("extraction", {})
        if extraction.get("prompt_template"):
            self._extraction_prompt.setPlainText(str(extraction["prompt_template"]))
        if extraction.get("chunk_strategy"):
            ci = self._chunk_strategy.findData(extraction["chunk_strategy"])
            if ci >= 0:
                self._chunk_strategy.setCurrentIndex(ci)
        if extraction.get("max_tokens_per_chunk"):
            self._max_tokens_per_chunk_spin.setValue(int(extraction["max_tokens_per_chunk"]))

    def _build_provider_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Provider 类型选择
        provider_group = QGroupBox(_("选择 AI 服务"))
        provider_layout = QFormLayout(provider_group)

        self._provider_type = QComboBox()
        self._provider_type.addItem(_("关闭 AI（不使用）"), "disabled")
        self._provider_type.addItem(_("OpenAI"), "openai")
        self._provider_type.addItem(_("OpenAI 兼容服务"), "openai_compatible")
        self._provider_type.addItem(_("Ollama 本地模型"), "local")
        self._provider_type.addItem(_("自定义 API 地址"), "custom")
        self._provider_type.currentIndexChanged.connect(self._on_provider_changed)
        provider_layout.addRow(_("服务类型："), self._provider_type)

        self._base_url = QLineEdit()
        self._base_url.setPlaceholderText("https://api.openai.com/v1")
        provider_layout.addRow(_("API 地址："), self._base_url)

        self._model_name = QLineEdit()
        self._model_name.setPlaceholderText("gpt-4o")
        provider_layout.addRow(_("模型名称："), self._model_name)

        self._api_key = QLineEdit()
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._api_key.setPlaceholderText(_("输入 API Key 或 secret://引用"))
        provider_layout.addRow(_("API Key："), self._api_key)

        # 连接操作
        action_layout = QHBoxLayout()
        self._test_button = QPushButton(_("🔍 测试连接"))
        self._test_button.clicked.connect(self._test_connection)
        action_layout.addWidget(self._test_button)

        self._list_models_button = QPushButton(_("📋 获取模型列表"))
        self._list_models_button.clicked.connect(self._list_models)
        action_layout.addWidget(self._list_models_button)
        self._generation_button = QPushButton(_("单次小额生成验收"))
        self._generation_button.clicked.connect(self._test_generation)
        action_layout.addWidget(self._generation_button)

        self._status_label = QLabel("")
        self._status_label.setWordWrap(True)
        provider_layout.addRow("", action_layout)
        provider_layout.addRow(self._status_label)

        layout.addWidget(provider_group)
        self._allow_private_endpoint = QCheckBox(_("允许所选本地或内网模型地址（仍限定端点域与端口）"))
        self._allow_private_endpoint.setAccessibleName(_("允许本地模型诊断"))
        layout.addWidget(self._allow_private_endpoint)
        probe_form = QFormLayout()
        self._input_rate = QDoubleSpinBox()
        self._output_rate = QDoubleSpinBox()
        self._probe_cost = QDoubleSpinBox()
        for control in (self._input_rate, self._output_rate, self._probe_cost):
            control.setDecimals(6)
            control.setRange(0, 1000000)
        self._probe_cost.setValue(.001)
        probe_form.addRow(_("输入单价 / 百万 Token"), self._input_rate)
        probe_form.addRow(_("输出单价 / 百万 Token"), self._output_rate)
        probe_form.addRow(_("本次生成验收费用上限"), self._probe_cost)
        self._pricing_confirmed = QCheckBox(_("已核对模型单价，0 表示免费；估算不替代服务商账单"))
        probe_form.addRow(self._pricing_confirmed)
        self._structured_probe = QCheckBox(_("本次验证严格 JSON Schema 支持"))
        probe_form.addRow(self._structured_probe)
        probe_form.addRow(QLabel(_("连接检查仅读取模型列表。生成验收只发送固定测试文本，最多一次请求、16 输出 Token；可能产生费用。")))
        layout.addLayout(probe_form)

        # 超时和响应设置
        perf_group = QGroupBox(_("性能设置"))
        perf_layout = QFormLayout(perf_group)

        self._timeout = QSpinBox()
        self._timeout.setRange(10, 300)
        self._timeout.setValue(60)
        self._timeout.setSuffix(_(" 秒"))
        perf_layout.addRow(_("请求超时："), self._timeout)

        self._max_tokens = QSpinBox()
        self._max_tokens.setRange(100, 128000)
        self._max_tokens.setValue(4096)
        self._max_tokens.setSingleStep(1024)
        self._max_tokens.setSuffix(_(" tokens"))
        perf_layout.addRow(_("最大响应长度："), self._max_tokens)
        layout.addWidget(perf_group)

        layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setAccessibleName(_("AI 服务设置，可滚动"))
        scroll.setWidget(widget)
        return scroll

    def _build_privacy_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        content_group = QGroupBox(_("内容发送控制"))
        content_layout = QFormLayout(content_group)

        self._allow_page_text = QCheckBox(_("允许发送网页正文给 AI"))
        self._allow_page_text.setToolTip(_("用于字段理解和内容分析。关闭时只发送页面 URL 和元数据。"))
        content_layout.addRow("", self._allow_page_text)

        self._allow_pdf_content = QCheckBox(_("允许发送 PDF 内容给 AI"))
        self._allow_pdf_content.setToolTip(_("用于 PDF 字段提取和文档理解。可能包含敏感文档内容。"))
        content_layout.addRow("", self._allow_pdf_content)

        self._allow_screenshots = QCheckBox(_("允许发送截图给 AI"))
        self._allow_screenshots.setToolTip(_("用于可视化页面分析和选择器推荐。截屏可能包含登录界面信息。"))
        content_layout.addRow("", self._allow_screenshots)

        self._allow_cookies = QCheckBox(_("允许发送 Cookie 和认证信息"))
        self._allow_cookies.setChecked(False)
        self._allow_cookies.setToolTip(_("绝大多数情况下应关闭。仅在需要 AI 帮助调整认证流程时临时开启。"))
        content_layout.addRow("", self._allow_cookies)

        layout.addWidget(content_group)

        audit_group = QGroupBox(_("审计与成本控制"))
        audit_layout = QFormLayout(audit_group)

        self._cost_limit = QDoubleSpinBox()
        self._cost_limit.setDecimals(6)
        self._cost_limit.setRange(0, 1000)
        self._cost_limit.setValue(0)
        self._cost_limit.setSpecialValueText(_("无限制"))
        self._cost_limit.setSuffix(_("（按模型单价币种）"))
        self._cost_limit.setToolTip(_("单次任务 AI 调用的费用上限；超过后自动回退到本地模式。"))
        audit_layout.addRow(_("费用上限："), self._cost_limit)

        self._log_ai_calls = QCheckBox(_("记录 AI 调用详情"))
        self._log_ai_calls.setChecked(True)
        self._log_ai_calls.setToolTip(_("记录模型、Prompt 版本、参数、调用时间和配置差异，用于审计。"))
        audit_layout.addRow("", self._log_ai_calls)

        layout.addWidget(audit_group)
        layout.addStretch()
        return widget

    def _build_routing_tab(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)

        layout.addWidget(QLabel(_("为不同能力指定不同的 AI Provider：\n" +

                                _("留空表示使用默认 Provider 或关闭该能力的 AI 增强。"))))
        routing_group = QGroupBox(_("能力路由"))
        routing_layout = QFormLayout(routing_group)

        self._route_field_design = QComboBox()
        self._route_field_design.setEditable(True)
        self._route_field_design.setToolTip(_("字段理解和推荐使用的 Provider"))
        routing_layout.addRow(_("字段设计："), self._route_field_design)

        self._route_content_analysis = QComboBox()
        self._route_content_analysis.setEditable(True)
        self._route_content_analysis.setToolTip(_("内容分析和摘要使用的 Provider"))
        routing_layout.addRow(_("内容分析："), self._route_content_analysis)

        self._route_task_planning = QComboBox()
        self._route_task_planning.setEditable(True)
        self._route_task_planning.setToolTip(_("任务设计和配置生成使用的 Provider"))
        routing_layout.addRow(_("任务设计："), self._route_task_planning)

        self._route_pdf_ocr = QComboBox()
        self._route_pdf_ocr.setEditable(True)
        self._route_pdf_ocr.setToolTip(_("PDF 文档理解和 OCR 后处理使用的 Provider"))
        routing_layout.addRow(_("PDF 理解："), self._route_pdf_ocr)

        layout.addWidget(routing_group)
        layout.addStretch()
        return widget

    def _build_extraction_tab(self) -> QWidget:
        """构建智能提取设置标签页。"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        prompt_group = QGroupBox(_("默认 Prompt 模板"))
        prompt_layout = QVBoxLayout(prompt_group)
        self._extraction_prompt = QPlainTextEdit()
        self._extraction_prompt.setPlaceholderText(_(
            _("自定义 AI 提取 prompt 模板。\n") +

            _("可用变量: {fields_spec}, {html_chunk}\n") +

            _("留空使用内置默认模板。")
        ))
        self._extraction_prompt.setMaximumHeight(200)
        prompt_layout.addWidget(self._extraction_prompt)
        layout.addWidget(prompt_group)

        strategy_group = QGroupBox(_("分块策略"))
        strategy_layout = QFormLayout(strategy_group)

        self._chunk_strategy = QComboBox()
        self._chunk_strategy.addItem(_("自动（推荐）"), "auto")
        self._chunk_strategy.addItem(_("按标题分块"), "heading")
        self._chunk_strategy.addItem(_("按固定字数分块"), "fixed_chunk")
        strategy_layout.addRow(_("分块方式："), self._chunk_strategy)

        self._max_tokens_per_chunk_spin = QSpinBox()
        self._max_tokens_per_chunk_spin.setRange(500, 32000)
        self._max_tokens_per_chunk_spin.setValue(4000)
        self._max_tokens_per_chunk_spin.setSingleStep(1000)
        self._max_tokens_per_chunk_spin.setSuffix(_(" tokens"))
        self._max_tokens_per_chunk_spin.setToolTip(_("每次 LLM 调用发送的最大 token 数，超过则自动分块"))
        strategy_layout.addRow(_("每次请求最大 Token："), self._max_tokens_per_chunk_spin)

        layout.addWidget(strategy_group)
        layout.addStretch()
        return widget

    def _on_provider_changed(self, index: int) -> None:
        """Provider 类型变更时更新默认值。"""
        provider_type = str(self._provider_type.currentData())
        defaults = {
            "openai": ("https://api.openai.com/v1", "gpt-4o"),
            "openai_compatible": ("https://api.openai.com/v1", ""),
            "local": ("http://localhost:11434/v1", "llama3"),
            "custom": ("", ""),
            "disabled": ("", ""),
        }
        api_url, model = defaults.get(provider_type, ("", ""))
        if api_url:
            self._base_url.setText(api_url)
        if model:
            self._model_name.setText(model)
        is_enabled = provider_type != "disabled"
        self._base_url.setEnabled(is_enabled)
        self._model_name.setEnabled(is_enabled)
        self._api_key.setEnabled(is_enabled and provider_type != "local")
        self._test_button.setEnabled(is_enabled)
        self._list_models_button.setEnabled(is_enabled)
        self._generation_button.setEnabled(is_enabled)

    def _resolve_workspace(self) -> Path:
        """C43：活动任务工作区为空时回退到用户数据目录，保证探测类操作仍可进行。"""
        if self._workspace is not None:
            return self._workspace
        fallback = (
            Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation))
            / "omnicrawler"
            / "ai_probe"
        )
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback

    def _probe_pricing(self) -> dict[str, Any]:
        if not self._pricing_confirmed.isChecked():
            return {}
        previous = self._ai_config.get("providers", {}).get("default", {}).get("pricing", {})
        return {**previous, "input_per_million": self._input_rate.value(), "output_per_million": self._output_rate.value()}

    def _test_generation(self) -> None:
        if self._active_workers:
            return
        try:
            prediction = estimate(self._probe_pricing())
            if prediction["estimated_cost"] is None or self._probe_cost.value() <= 0 or prediction["estimated_cost"] > self._probe_cost.value():
                raise ValueError(_("请核对单价，并设置大于预估费用的正数验收预算。"))
        except ValueError as exc:
            self._status_label.setText(str(exc))
            return
        base, key, model = self._base_url.text().strip().rstrip("/"), self._api_key.text().strip(), self._model_name.text().strip()
        if not base or not model:
            self._status_label.setText(_("请先填写地址和模型。"))
            return
        if QMessageBox.question(self, _("确认小额生成验收"), _("模型：{0}\n一次请求，最多16输出Token\n预估：{1}；本次上限：{2}\n仅发送固定测试文本；费用以服务商账单为准。").format(
                model, prediction["estimated_cost"], self._probe_cost.value()), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._probe_snapshot = (base, key, model)
        self._test_worker = AITestWorker(base, key, model, self._timeout.value(), self._resolve_workspace(),
            generate_options={"pricing": self._probe_pricing(), "maximum_cost": self._probe_cost.value(), "structured": self._structured_probe.isChecked()},
            allow_private=self._allow_private_endpoint.isChecked())
        self._test_worker.setParent(self)
        self._test_worker.test_done.connect(self._on_test_complete)
        self._track_worker(self._test_worker)
        self._status_label.setText(_("正在进行有预算的生成验收…"))
        self._test_worker.start()

    def _track_worker(self, worker: QThread) -> None:
        self._active_workers.append(worker)
        self._test_button.setEnabled(False)
        self._generation_button.setEnabled(False)
        self._list_models_button.setEnabled(False)
        worker.finished.connect(self._worker_finished)

    def _worker_finished(self) -> None:
        worker = self.sender()
        if isinstance(worker, QThread) and worker in self._active_workers:
            self._active_workers.remove(worker)
            worker.deleteLater()
        self._test_button.setEnabled(True)
        self._generation_button.setEnabled(True)
        self._list_models_button.setEnabled(True)
        if self._close_pending and not self._active_workers:
            self.close()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._active_workers:
            self._close_pending = True
            for worker in self._active_workers:
                worker.requestInterruption()
            self._status_label.setText(_("正在取消诊断，结束后关闭。"))
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if self._active_workers:
            self.close()
        else:
            super().reject()

    def _test_connection(self) -> None:
        if self._active_workers:
            return
        """测试 AI 服务连接。"""
        base_url = self._base_url.text().strip().rstrip("/")
        api_key = self._api_key.text().strip()
        model = self._model_name.text().strip()
        timeout = self._timeout.value()

        if not base_url or not model:
            self._status_label.setText(_("❌ 请填写 API 地址和模型名称"))
            return
        if self._workspace is None:
            self._status_label.setText(_("⚠ 未检测到活动任务工作区，将使用本地临时目录进行探测"))

        self._test_button.setEnabled(False)
        self._status_label.setText(_("正在测试连接…"))

        self._probe_snapshot = (base_url, api_key, model)
        self._test_worker = AITestWorker(base_url, api_key, model, timeout, self._resolve_workspace(), allow_private=self._allow_private_endpoint.isChecked())
        self._test_worker.setParent(self)
        self._test_worker.test_done.connect(self._on_test_complete)
        self._track_worker(self._test_worker)
        self._test_worker.start()

    def _on_test_complete(self, success: bool, message: str) -> None:
        if self._close_pending:
            return
        if self._probe_snapshot != (self._base_url.text().strip().rstrip("/"), self._api_key.text().strip(), self._model_name.text().strip()):
            self._status_label.setText(_("模型配置已变化，请重新检查。"))
            return
        self._test_button.setEnabled(True)
        icon = "✅" if success else "❌"
        self._status_label.setText(f"{icon} {message}")

    def _list_models(self) -> None:
        if self._active_workers:
            return
        """获取可用模型列表。"""
        base_url = self._base_url.text().strip().rstrip("/")
        api_key = self._api_key.text().strip()
        timeout = self._timeout.value()

        if not base_url:
            self._status_label.setText(_("❌ 请填写 API 地址"))
            return
        if self._workspace is None:
            self._status_label.setText(_("⚠ 未检测到活动任务工作区，将使用本地临时目录进行探测"))

        self._list_models_button.setEnabled(False)
        self._status_label.setText(_("正在获取模型列表…"))

        self._models_snapshot = (base_url, api_key)
        self._models_worker = AIListModelsWorker(base_url, api_key, timeout, self._resolve_workspace(), allow_private=self._allow_private_endpoint.isChecked())
        self._models_worker.setParent(self)
        self._models_worker.models_ready.connect(self._on_models_ready)
        self._track_worker(self._models_worker)
        self._models_worker.start()

    def _on_models_ready(self, models: list[str], error: str) -> None:
        if self._close_pending:
            return
        if self._models_snapshot != (self._base_url.text().strip().rstrip("/"), self._api_key.text().strip()):
            self._status_label.setText(_("模型地址已变化，请重新获取列表。"))
            return
        self._list_models_button.setEnabled(True)
        if error:
            self._status_label.setText(f"❌ {error}")
            return
        if models:
            # Discovery must not silently replace a model selected by the user.
            models_text = "\n".join(models[:20])
            if len(models) > 20:
                models_text += _(f"\n… 还有 {len(models) - 20} 个模型")
            QMessageBox.information(
                self, _("可用模型"), f"共找到 {len(models)} 个模型：\n{models_text}"
            )
            self._status_label.setText(_(f"✅ 已加载 {len(models)} 个模型，已选择 {models[0]}"))
        else:
            self._status_label.setText(_("⚠ 未找到可用模型"))

    def _save_and_accept(self) -> None:
        if self._active_workers:
            self._status_label.setText(_("请等待诊断结束，或取消后关闭。"))
            return
        """保存设置并关闭对话框。"""
        provider_type = str(self._provider_type.currentData())
        if provider_type == "disabled":
            self._ai_config["mode"] = "disabled"
        else:
            # C42：保存前必填校验，避免存下「已启用但填不全」导致静默 None 的配置
            base_url = self._base_url.text().strip().rstrip("/")
            model = self._model_name.text().strip()
            if not base_url or not model:
                QMessageBox.warning(
                    self,
                    _("配置不完整"),
                    _("已启用 AI 但未填写 API 地址或模型名称，无法保存。请补全后再保存，或先用「测试连接」验证。"),
                )
                return
            self._ai_config["mode"] = "enabled"
            self._ai_config["default_provider"] = "default"
            previous = self._ai_config.setdefault("providers", {}).get("default", {})
            self._ai_config["providers"]["default"] = {**previous,
                "type": provider_type,
                "base_url": base_url,
                "model": model,
                "api_key": self._api_key.text().strip(),
                "timeout_seconds": self._timeout.value(),
                "pricing": self._probe_pricing(),
                "diagnostics_allow_private": self._allow_private_endpoint.isChecked(),
            }
            # 安全与隐私设置
            self._ai_config["privacy"] = {
                "allow_page_text": self._allow_page_text.isChecked(),
                "allow_pdf_content": self._allow_pdf_content.isChecked(),
                "allow_screenshots": self._allow_screenshots.isChecked(),
                "allow_cookies": self._allow_cookies.isChecked(),
            }
            self._ai_config["budget"] = {**self._ai_config.get("budget", {}),
                "max_cost": self._cost_limit.value(),
                "maximum_cost": self._cost_limit.value(),
                "max_tokens_per_request": self._max_tokens.value(),
                "log_calls": self._log_ai_calls.isChecked(),
            }
            # 能力路由
            routing = {}
            for capability, combo in [
                ("field_designer", self._route_field_design),
                ("content_analysis", self._route_content_analysis),
                ("task_planning", self._route_task_planning),
                ("pdf_ocr", self._route_pdf_ocr),
            ]:
                value = combo.currentText().strip()
                if value:
                    routing[capability] = value
            if routing:
                self._ai_config["routing"] = routing

            # 智能提取设置
            extraction_prompt = self._extraction_prompt.toPlainText().strip()
            self._ai_config["extraction"] = {
                "prompt_template": extraction_prompt if extraction_prompt else None,
                "chunk_strategy": str(self._chunk_strategy.currentData()),
                "max_tokens_per_chunk": self._max_tokens_per_chunk_spin.value(),
            }
        self.accept()
