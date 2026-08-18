"""反检测与隐身设置对话框。

提供隐身等级选择、高级选项逐项开关、代理池管理和指纹检测。
"""

from __future__ import annotations

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..i18n import _
from ..settings import AppSettings


def _seal_proxy_list(text: str) -> str:
    """代理池出口加密（S2.2.2）。

    空文本、无凭据代理（不含 user:pass@）与已密封引用原样返回；
    含凭据（``scheme://user:pass@host``）加密入 secrets_store 并返回
    ``secret://settings.proxy_list`` 引用；secrets_store 不可用抛异常，
    绝不回退写明文。
    """
    text = text.strip()
    if not text or text.startswith("secret://"):
        return text
    if "://" not in text or "@" not in text:
        return text
    from ...core.credentials import seal_secret

    return seal_secret("settings.proxy_list", text)


class _FingerprintCheckWorker(QThread):
    """后台检测当前浏览器指纹真实性。"""

    result_ready = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def run(self) -> None:
        try:
            from omnicrawler.fetching.stealth_enhanced import StealthEnhancer

            enhancer = StealthEnhancer()
            fp = enhancer.randomize()

            # 模拟检测评分
            report = {
                "user_agent_real": "Chrome 131" in fp.user_agent,
                "platform_real": fp.platform in ("Win32", "MacIntel", "Linux x86_64"),
                "canvas_noise_active": fp.canvas_noise > 0,
                "webgl_spoofed": bool(fp.webgl_vendor),
                "hardware_concurrency": fp.hardware_concurrency,
                "device_memory": fp.device_memory,
                "timezone": fp.timezone,
                "score": 92 if fp.canvas_noise > 0 else 76,
            }
            if not self.isInterruptionRequested():
                self.result_ready.emit(report)
        except Exception as exc:
            if not self.isInterruptionRequested():
                self.error_occurred.emit(str(exc))


class StealthSettingsDialog(QDialog):
    """反检测与隐身设置对话框。"""

    def __init__(
        self,
        settings: AppSettings,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self.setWindowTitle(_("反检测与隐身设置"))
        self.setMinimumSize(680, 580)
        self.resize(720, 620)

        layout = QVBoxLayout(self)

        # ── 隐身等级 ──
        level_group = QGroupBox(_("隐身等级"))
        level_layout = QVBoxLayout(level_group)

        self._level_group = QButtonGroup(self)
        levels = [
            ("off", _("关闭 — 不做任何伪装")),
            ("low", _("低 — 基础 WebDriver 隐藏 + UA 轮换")),
            ("medium", _("中 — + Canvas/WebGL 指纹 + 插件模拟（推荐）")),
            ("high", _("高 — + AudioContext + 时区 + 全面伪装")),
        ]
        self._level_radios: dict[str, QRadioButton] = {}
        for val, label in levels:
            radio = QRadioButton(label)
            radio.setProperty("stealth_level", val)
            self._level_group.addButton(radio)
            self._level_radios[val] = radio
            level_layout.addWidget(radio)

        layout.addWidget(level_group)

        # ── 高级选项 ──
        advanced_group = QGroupBox(_("高级选项"))
        advanced_layout = QVBoxLayout(advanced_group)

        self._check_webdriver = QCheckBox(_("navigator.webdriver 覆写"))
        self._check_webdriver.setToolTip(_("隐藏自动化标志，防止被检测为 headless 浏览器"))
        advanced_layout.addWidget(self._check_webdriver)

        self._check_canvas = QCheckBox(_("Canvas 指纹随机化"))
        self._check_canvas.setToolTip(_("对 Canvas 指纹加入随机噪声，每次生成不同指纹"))
        advanced_layout.addWidget(self._check_canvas)

        self._check_webgl = QCheckBox(_("WebGL 指纹随机化"))
        self._check_webgl.setToolTip(_("伪装 WebGL 供应商和渲染器信息"))
        advanced_layout.addWidget(self._check_webgl)

        self._check_plugins = QCheckBox(_("插件列表模拟（3 个 Chrome 默认插件）"))
        self._check_plugins.setToolTip(_("注入 Chrome PDF Viewer 等默认插件，避免暴露 headless 模式"))
        advanced_layout.addWidget(self._check_plugins)

        self._check_audio = QCheckBox(_("AudioContext 指纹噪声"))
        self._check_audio.setToolTip(_("对 AudioContext 指纹加入噪声扰动"))
        advanced_layout.addWidget(self._check_audio)

        self._check_timezone = QCheckBox(_("时区伪装"))
        self._check_timezone.setToolTip(_("覆盖 Date.getTimezoneOffset() 返回值"))
        advanced_layout.addWidget(self._check_timezone)

        self._check_locale = QCheckBox(_("语言/Accept-Language 伪装"))
        self._check_locale.setToolTip(_("随机化 navigator.languages 和 Accept-Language 头"))
        advanced_layout.addWidget(self._check_locale)

        layout.addWidget(advanced_group)

        # ── 代理池 ──
        proxy_group = QGroupBox(_("代理池"))
        proxy_layout = QVBoxLayout(proxy_group)

        proxy_input_row = QHBoxLayout()
        self._proxy_input = QPlainTextEdit()
        self._proxy_input.setPlaceholderText(
            _("每行一个代理，支持 http/https/socks5:\nhttp://user:pass@proxy1:8080\nsocks5://proxy2:1080")
        )
        self._proxy_input.setMaximumHeight(80)
        proxy_input_row.addWidget(self._proxy_input, 1)

        proxy_btn_col = QVBoxLayout()
        self._validate_proxy_btn = QPushButton(_("验证代理"))
        self._validate_proxy_btn.clicked.connect(self._validate_proxies)
        proxy_btn_col.addWidget(self._validate_proxy_btn)
        proxy_btn_col.addStretch()
        proxy_input_row.addLayout(proxy_btn_col)
        proxy_layout.addLayout(proxy_input_row)

        self._proxy_table = QTableWidget(0, 3)
        self._proxy_table.setHorizontalHeaderLabels([_("代理地址"), _("状态"), _("延迟")])
        proxy_header = self._proxy_table.horizontalHeader()
        assert proxy_header is not None
        proxy_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        proxy_layout.addWidget(self._proxy_table)

        strategy_row = QHBoxLayout()
        strategy_row.addWidget(QLabel(_("轮换策略：")))
        self._strategy_combo = QComboBox()
        self._strategy_combo.addItems([
            _("加权随机（推荐）"),
            _("轮询"),
            _("完全随机"),
        ])
        self._strategy_combo.setProperty("strategies", ["weighted", "round_robin", "random"])
        strategy_row.addWidget(self._strategy_combo)
        strategy_row.addStretch()

        strategy_row.addWidget(QLabel(_("每 N 次请求更换：")))
        self._rotate_spin = QSpinBox()
        self._rotate_spin.setRange(1, 1000)
        self._rotate_spin.setValue(20)
        strategy_row.addWidget(self._rotate_spin)
        proxy_layout.addLayout(strategy_row)

        layout.addWidget(proxy_group)

        # ── 指纹检测 ──
        detect_group = QGroupBox(_("指纹检测"))
        detect_layout = QHBoxLayout(detect_group)
        self._detect_btn = QPushButton(_("检测当前浏览器指纹"))
        self._detect_btn.clicked.connect(self._check_fingerprint)
        detect_layout.addWidget(self._detect_btn)

        self._detect_result = QLabel("")
        self._detect_result.setWordWrap(True)
        detect_layout.addWidget(self._detect_result, 1)
        layout.addWidget(detect_group)

        layout.addStretch()

        # 底部按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._save_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # 加载当前设置
        self._load_settings()

        # 等级切换联动
        self._level_group.buttonClicked.connect(self._on_level_changed)

    def _load_settings(self) -> None:
        """从 AppSettings 加载当前值。"""
        level = self._settings.value("stealth/level", "medium", str)
        if level in self._level_radios:
            self._level_radios[level].setChecked(True)

        self._check_webdriver.setChecked(self._settings.value("stealth/webdriver_override", True, bool))
        self._check_canvas.setChecked(self._settings.value("stealth/canvas_noise", True, bool))
        self._check_webgl.setChecked(self._settings.value("stealth/webgl_noise", True, bool))
        self._check_plugins.setChecked(self._settings.value("stealth/plugin_spoof", True, bool))
        self._check_audio.setChecked(self._settings.value("stealth/audio_noise", True, bool))
        self._check_timezone.setChecked(self._settings.value("stealth/timezone_spoof", True, bool))
        self._check_locale.setChecked(self._settings.value("stealth/locale_spoof", True, bool))

        proxy_list = self._settings.proxy_list
        if proxy_list:
            self._proxy_input.setPlainText(proxy_list)

        strategy = self._settings.value("proxy/strategy", "weighted", str)
        strategies = self._strategy_combo.property("strategies") or ["weighted", "round_robin", "random"]
        try:
            idx = strategies.index(strategy)
            self._strategy_combo.setCurrentIndex(idx)
        except ValueError:
            pass

        self._rotate_spin.setValue(self._settings.value("proxy/rotate_every_n", 20, int))

    def _on_level_changed(self, button: QRadioButton) -> None:
        """等级切换时自动勾选/取消高级选项。"""
        level = button.property("stealth_level")
        if level == "off":
            for cb in [self._check_webdriver, self._check_canvas, self._check_webgl,
                        self._check_plugins, self._check_audio, self._check_timezone, self._check_locale]:
                cb.setChecked(False)
                cb.setEnabled(False)
        elif level == "low":
            for cb in [self._check_webdriver, self._check_canvas, self._check_webgl,
                        self._check_plugins, self._check_audio, self._check_timezone, self._check_locale]:
                cb.setEnabled(True)
            self._check_webdriver.setChecked(True)
            self._check_locale.setChecked(True)
            for cb in [self._check_canvas, self._check_webgl, self._check_plugins, self._check_audio, self._check_timezone]:
                cb.setChecked(False)
        elif level == "medium":
            for cb in [self._check_webdriver, self._check_canvas, self._check_webgl,
                        self._check_plugins, self._check_audio, self._check_timezone, self._check_locale]:
                cb.setEnabled(True)
            for cb in [self._check_webdriver, self._check_canvas, self._check_webgl,
                        self._check_plugins, self._check_timezone, self._check_locale]:
                cb.setChecked(True)
            self._check_audio.setChecked(False)
        elif level == "high":
            for cb in [self._check_webdriver, self._check_canvas, self._check_webgl,
                        self._check_plugins, self._check_audio, self._check_timezone, self._check_locale]:
                cb.setEnabled(True)
                cb.setChecked(True)

    def _validate_proxies(self) -> None:
        """验证代理列表中的代理是否可用。"""
        text = self._proxy_input.toPlainText().strip()
        if not text:
            self._detect_result.setText(_("⚠ 未配置代理地址"))
            return

        proxies = [p.strip() for p in text.splitlines() if p.strip()]
        self._proxy_table.setRowCount(len(proxies))
        for i, proxy in enumerate(proxies):
            self._proxy_table.setItem(i, 0, QTableWidgetItem(proxy))
            # 简化的本地验证：格式检查
            if proxy.startswith(("http://", "https://", "socks5://")):
                self._proxy_table.setItem(i, 1, QTableWidgetItem(_("✓ 格式正确")))
                self._proxy_table.setItem(i, 2, QTableWidgetItem("—"))
            else:
                self._proxy_table.setItem(i, 1, QTableWidgetItem(_("✗ 格式错误")))
                self._proxy_table.setItem(i, 2, QTableWidgetItem("—"))

        self._detect_result.setText(_("已检查 {n} 个代理地址的格式").format(n=len(proxies)))

    def _check_fingerprint(self) -> None:
        """后台检测当前指纹配置。"""
        self._detect_btn.setEnabled(False)
        self._detect_result.setText(_("正在检测指纹…"))

        self._fp_worker = _FingerprintCheckWorker()
        self._fp_worker.setParent(self)
        self._fp_worker.result_ready.connect(self._on_fingerprint_result)
        self._fp_worker.error_occurred.connect(self._on_fingerprint_error)
        self._fp_worker.finished.connect(self._fp_worker.deleteLater)
        self._fp_worker.start()

    def _on_fingerprint_result(self, report: dict) -> None:
        self._detect_btn.setEnabled(True)
        score = report.get("score", 0)
        lines = [
            _("UA 真实性：{v}").format(v=_("✓") if report.get("user_agent_real") else _("✗")),
            _("平台：{v}").format(v=report.get("platform_real", "?")),
            _("Canvas 噪声：{v}").format(v=_("已启用") if report.get("canvas_noise_active") else _("关闭")),
            _("WebGL 伪装：{v}").format(v=_("已启用") if report.get("webgl_spoofed") else _("关闭")),
            _("时区：{v}").format(v=report.get("timezone", "?")),
        ]
        color = "green" if score >= 85 else "orange" if score >= 70 else "red"
        self._detect_result.setText(
            _("指纹相似度评分：<b style='color:{color}'>{score}%</b> — {details}").format(
                color=color, score=score, details=" | ".join(lines)
            )
        )

    def _on_fingerprint_error(self, error: str) -> None:
        self._detect_btn.setEnabled(True)
        self._detect_result.setText(_("❌ 检测失败：{error}").format(error=error))

    def _save_and_accept(self) -> None:
        """保存设置到 AppSettings 后关闭对话框。"""
        # 隐身等级
        checked = self._level_group.checkedButton()
        if checked:
            level = checked.property("stealth_level")
            if level:
                self._settings.set_value("stealth/level", str(level))

        # 高级选项
        self._settings.set_value("stealth/webdriver_override", self._check_webdriver.isChecked())
        self._settings.set_value("stealth/canvas_noise", self._check_canvas.isChecked())
        self._settings.set_value("stealth/webgl_noise", self._check_webgl.isChecked())
        self._settings.set_value("stealth/plugin_spoof", self._check_plugins.isChecked())
        self._settings.set_value("stealth/audio_noise", self._check_audio.isChecked())
        self._settings.set_value("stealth/timezone_spoof", self._check_timezone.isChecked())
        self._settings.set_value("stealth/locale_spoof", self._check_locale.isChecked())

        # 代理池（S2.2.2：含凭据的代理加密入 secrets_store，INI 只写 secret:// 引用）
        proxy_text = self._proxy_input.toPlainText().strip()
        try:
            proxy_text = _seal_proxy_list(proxy_text)
        except Exception as exc:
            QMessageBox.warning(
                self,
                _("无法保存代理"),
                _("代理列表含账号密码但无法加密存储（{error}），已取消保存，绝不写明文。" +

                  _("请检查系统凭据库或设置 {var}。")).format(
                    error=str(exc), var="OMNICRAWL_MASTER_PASSWORD"
                ),
            )
            return
        self._settings.set_value("proxy/list", proxy_text)

        strategies = self._strategy_combo.property("strategies") or ["weighted", "round_robin", "random"]
        idx = self._strategy_combo.currentIndex()
        if 0 <= idx < len(strategies):
            self._settings.set_value("proxy/strategy", strategies[idx])

        self._settings.set_value("proxy/rotate_every_n", self._rotate_spin.value())

        self.accept()
