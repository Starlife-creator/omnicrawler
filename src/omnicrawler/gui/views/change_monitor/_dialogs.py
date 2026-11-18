"""变更监控新建/编辑规则对话框与差异详情查看对话框。"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from omnicrawler.gui.design_system import FONT_FAMILY_MONO, SPACING, ThemeManager, scaled_font_px
from omnicrawler.gui.i18n import _

from ._workers import CONDITION_OPTIONS, INTERVAL_OPTIONS, _ProbeWorker


class NewRuleDialog(QDialog):
    """新建或编辑监控规则。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        rule_data: dict | None = None,
        *, fetcher: Any = None,
    ) -> None:
        super().__init__(parent)
        self._fetcher = fetcher
        self._probe_worker: _ProbeWorker | None = None
        self._probe_result: dict[str, Any] | None = None
        self._probe_error = ""
        self._pending_done: int | None = None
        self._delete_after_probe = False
        self.setAccessibleName(_("新建监控规则"))
        self.setWindowTitle(_("新建变更监控规则") if rule_data is None else _("编辑变更监控规则"))
        self.setMinimumSize(480, 420)
        self._rule_data = rule_data

        outer = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setAccessibleName(_("监控规则设置"))
        content = QWidget(scroll)
        scroll.setWidget(content)
        outer.addWidget(scroll)
        layout = QFormLayout(content)
        layout.setSpacing(10)

        # 规则名称
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText(_("如: 监控 Product Hunt 首页"))
        layout.addRow(_("规则名称:"), self._name_edit)

        # 目标 URL
        url_row = QHBoxLayout()
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText("https://example.com/page")
        url_row.addWidget(self._url_edit)
        probe_btn = QPushButton(_("探测"))
        probe_btn.setToolTip(_("测试 URL 是否可达"))
        probe_btn.clicked.connect(self._probe_url)
        url_row.addWidget(probe_btn)
        layout.addRow(_("目标 URL:"), url_row)

        # CSS 选择器
        selector_row = QHBoxLayout()
        self._selector_edit = QLineEdit()
        self._selector_edit.setPlaceholderText(_("留空 = 监控整页；如: .post-item"))
        selector_row.addWidget(self._selector_edit)
        test_btn = QPushButton(_("测试"))
        test_btn.setToolTip(_("在当前页面测试选择器"))
        test_btn.clicked.connect(self._test_selector)
        selector_row.addWidget(test_btn)
        layout.addRow(_("CSS 选择器:"), selector_row)
        self._probe_status = QLabel(_("探测使用任务出口和 robots 策略；取消后等待当前请求收尾。"))
        self._probe_status.setWordWrap(True)
        self._probe_status.setAccessibleName(_("探测状态"))
        layout.addRow(self._probe_status)

        # 检测条件
        self._condition_combo = QComboBox()
        for value, label in CONDITION_OPTIONS:
            self._condition_combo.addItem(label, value)
        self._condition_combo.currentIndexChanged.connect(self._on_condition_changed)
        layout.addRow(_("检测条件:"), self._condition_combo)

        self._condition_value_edit = QLineEdit()
        self._condition_value_edit.setPlaceholderText(_("额外参数（文本/正则/值）"))
        self._condition_value_edit.setVisible(False)
        layout.addRow("", self._condition_value_edit)

        # 检查间隔
        self._interval_combo = QComboBox()
        for seconds, label in INTERVAL_OPTIONS:
            self._interval_combo.addItem(label, seconds)
        self._interval_combo.setCurrentIndex(3)  # 默认 30 分钟
        layout.addRow(_("检查间隔:"), self._interval_combo)

        self._consecutive = QSpinBox()
        self._consecutive.setRange(1, 100)
        self._consecutive.setAccessibleName(_("连续确认次数"))
        layout.addRow(_("连续确认次数:"), self._consecutive)
        self._cooldown = QSpinBox()
        self._cooldown.setRange(0, 604800)
        self._cooldown.setSuffix(_(" 秒"))
        self._cooldown.setAccessibleName(_("通知冷却时间"))
        layout.addRow(_("通知冷却:"), self._cooldown)
        self._absolute = QLineEdit()
        self._absolute.setPlaceholderText(_("可选；选区必须是数字"))
        self._absolute.setAccessibleName(_("最小绝对变化"))
        layout.addRow(_("最小绝对变化:"), self._absolute)
        self._relative = QLineEdit()
        self._relative.setPlaceholderText(_("可选；百分比，原值为零时需复核"))
        self._relative.setAccessibleName(_("最小相对变化百分比"))
        layout.addRow(_("最小相对变化 %:"), self._relative)
        self._ignored = QLineEdit()
        self._ignored.setPlaceholderText(_("用分号分隔 CSS 选择器"))
        self._ignored.setAccessibleName(_("忽略区域"))
        layout.addRow(_("忽略区域:"), self._ignored)
        self._webhook = QLineEdit()
        self._webhook.setPlaceholderText(_("可选；变化内容将发送至该地址"))
        self._webhook.setAccessibleName(_("Webhook 接收地址"))
        layout.addRow(_("Webhook:"), self._webhook)
        self._webhook_token = QLineEdit()
        self._webhook_token.setPlaceholderText(_("可选；secret:// 凭据引用"))
        self._webhook_token.setAccessibleName(_("Webhook 凭据引用"))
        layout.addRow(_("Webhook 凭据:"), self._webhook_token)
        # 通知方式
        notify_group = QGroupBox(_("通知方式"))
        notify_layout = QVBoxLayout(notify_group)
        self._notify_desktop = QCheckBox(_("桌面通知（系统托盘弹窗）"))
        self._notify_desktop.setChecked(True)
        notify_layout.addWidget(self._notify_desktop)
        self._notify_sound = QCheckBox(_("提示音"))
        notify_layout.addWidget(self._notify_sound)
        layout.addRow(notify_group)

        # 按钮
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate_and_accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

        # 按编辑数据填充
        if rule_data:
            self._load_data(rule_data)

    def _load_data(self, data: dict) -> None:
        self._name_edit.setText(data.get("name", ""))
        self._url_edit.setText(data.get("url", ""))
        self._selector_edit.setText(data.get("selector", ""))

        condition = data.get("condition", "changed")
        for idx in range(self._condition_combo.count()):
            if self._condition_combo.itemData(idx) == condition or condition.startswith(
                self._condition_combo.itemData(idx)
            ):
                self._condition_combo.setCurrentIndex(idx)
                break

        interval = data.get("check_interval", 3600)
        for idx in range(self._interval_combo.count()):
            if abs(self._interval_combo.itemData(idx) - interval) < 10:
                self._interval_combo.setCurrentIndex(idx)
                break

        self._consecutive.setValue(data.get("consecutive_checks", 1))
        self._cooldown.setValue(data.get("cooldown_seconds", 0))
        if data.get("minimum_absolute_change") is not None:
            self._absolute.setText(str(data["minimum_absolute_change"]))
        if data.get("minimum_relative_change") is not None:
            self._relative.setText(str(data["minimum_relative_change"] * 100))
        self._ignored.setText("; ".join(data.get("ignored_selectors", [])))
        self._webhook.setText(data.get("webhook_url", ""))
        self._webhook_token.setText(data.get("webhook_token_ref", ""))
        notify = data.get("notify_methods", ["desktop"])
        self._notify_desktop.setChecked("desktop" in notify)
        self._notify_sound.setChecked("sound" in notify)

    def _on_condition_changed(self, idx: int) -> None:
        cond_key = self._condition_combo.itemData(idx)
        self._condition_value_edit.setVisible(cond_key != "changed")

    def _probe_url(self) -> None:
        self._start_probe("")

    def _test_selector(self) -> None:
        selector = self._selector_edit.text().strip()
        if not selector:
            QMessageBox.warning(self, _("提示"), _("请先输入 CSS 选择器"))
            return
        self._start_probe(selector)

    def _start_probe(self, selector: str) -> None:
        url = self._url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, _("提示"), _("请先输入目标 URL"))
            return
        if self._probe_worker is not None:
            return
        self._probe_result, self._probe_error = None, ""
        worker = _ProbeWorker(url, selector, self._fetcher, self)
        self._probe_worker = worker
        worker.succeeded.connect(self._capture_probe_result)
        worker.failed.connect(self._capture_probe_error)
        worker.finished.connect(self._probe_stopped)
        self._probe_status.setText(_("探测中…可以取消；当前请求有超时上限。"))
        worker.start()

    @Slot(object)
    def _capture_probe_result(self, result: dict[str, Any]) -> None:
        if self.sender() is self._probe_worker:
            self._probe_result = result

    @Slot(str)
    def _capture_probe_error(self, error: str) -> None:
        if self.sender() is self._probe_worker:
            self._probe_error = error

    @Slot()
    def _probe_stopped(self) -> None:
        worker = self._probe_worker
        if worker is None or self.sender() is not worker:
            return
        self._probe_worker = None
        worker.deleteLater()
        if self._delete_after_probe:
            from PySide6.QtCore import QCoreApplication

            # Qt keeps deleteLater's posted flag after the intercepted event.
            # Repost the consumed event once the owned thread has stopped.
            QCoreApplication.postEvent(self, QEvent(QEvent.Type.DeferredDelete))
            return
        if self._pending_done is not None:
            super().done(self._pending_done)
            return
        if self._url_edit.text().strip() != worker.url or (
            worker.selector and self._selector_edit.text().strip() != worker.selector
        ):
            self._probe_status.setText(_("输入已变化，请重新探测。"))
            return
        if worker.isInterruptionRequested():
            self._probe_status.setText(_("探测已取消。"))
            return
        if self._probe_error:
            self._probe_status.setText(_("探测失败。"))
            QMessageBox.warning(self, _("探测失败"), self._probe_error)
            return
        result = self._probe_result or {}
        self._probe_status.setText(_("探测完成。"))
        if worker.selector:
            content = str(result.get("content", ""))
            if content.strip():
                QMessageBox.information(self, _("选择器测试成功"),
                                        _("匹配到 {0} 个字符\n\n{1}").format(len(content), content[:200]))
            else:
                QMessageBox.warning(self, _("选择器测试"), _("选择器未匹配到任何内容"))
        else:
            QMessageBox.information(self, _("探测成功"), _("URL 可达\nHTTP {0}").format(result.get("status", "未知")))

    def done(self, result: int) -> None:
        worker = self._probe_worker
        if worker is not None and worker.isRunning():
            self._pending_done = result
            worker.requestInterruption()
            self._probe_status.setText(_("取消中，等待当前请求收尾…"))
            return
        super().done(result)

    def event(self, event: QEvent) -> bool:
        worker = getattr(self, "_probe_worker", None)
        if event.type() == QEvent.Type.DeferredDelete and worker is not None and worker.isRunning():
            self._delete_after_probe = True
            worker.requestInterruption()
            return True
        return super().event(event)

    def _validate_and_accept(self) -> None:
        url = self._url_edit.text().strip()
        name = self._name_edit.text().strip()

        if not name:
            name = url[:50] if url else _("未命名规则")
            self._name_edit.setText(name)

        if not url:
            QMessageBox.warning(self, _("提示"), _("请输入目标 URL"))
            return

        try:
            from omnicrawler.scheduling.change_detector import MonitorRule
            MonitorRule.from_dict(self.get_rule_data())
        except (ValueError, TypeError) as exc:
            QMessageBox.warning(self, _("规则无效"), str(exc))
            return
        self.accept()

    def get_rule_data(self) -> dict:
        """返回用户填写的规则数据字典。"""
        cond_key = self._condition_combo.currentData()
        condition = cond_key
        if cond_key != "changed":
            extra = self._condition_value_edit.text().strip()
            condition = cond_key.replace("{{text}}", extra).replace("{{pattern}}", extra).replace("{{value}}", extra)

        notify_methods: list[str] = []
        if self._notify_desktop.isChecked():
            notify_methods.append("desktop")
        if self._notify_sound.isChecked():
            notify_methods.append("sound")

        result: dict = {
            "name": self._name_edit.text().strip() or _("未命名规则"),
            "url": self._url_edit.text().strip(),
            "selector": self._selector_edit.text().strip(),
            "condition": condition,
            "check_interval": self._interval_combo.currentData(),
            "notify_methods": notify_methods,
            "enabled": True,
            "consecutive_checks": self._consecutive.value(), "cooldown_seconds": self._cooldown.value(),
            "minimum_absolute_change": float(self._absolute.text()) if self._absolute.text().strip() else None,
            "minimum_relative_change": float(self._relative.text()) / 100 if self._relative.text().strip() else None,
            "ignored_selectors": [item.strip() for item in self._ignored.text().split(";") if item.strip()],
            "webhook_url": self._webhook.text().strip(), "webhook_token_ref": self._webhook_token.text().strip(),
        }

        if self._rule_data:
            result["rule_id"] = self._rule_data.get("rule_id", "")
            result["last_hash"] = self._rule_data.get("last_hash")
            result["last_checked"] = self._rule_data.get("last_checked")

        return result


# ── 变化详情对话框 ──────────────────────────────────────────────────

class ChangeEventDialog(QDialog):
    """显示变化详情的 diff 对话框。"""

    def __init__(self, event_data: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAccessibleName(_("变更详情"))
        self.setWindowTitle(_(f"变化详情 — {event_data.get('rule_name', '')}"))
        self.setMinimumSize(680, 480)

        layout = QVBoxLayout(self)

        # 摘要
        info_line = QHBoxLayout()
        info_line.addWidget(QLabel(f"URL: {event_data.get('url', '')}"))
        info_line.addStretch()
        detected = event_data.get("detected_at", "")
        if isinstance(detected, str):
            info_line.addWidget(QLabel(_(f"检测时间: {detected[:19]}")))
        layout.addLayout(info_line)

        summary = event_data.get("diff_summary", _("无摘要"))
        summary_label = QLabel(_(f"变化摘要: {summary}"))
        summary_label.setStyleSheet(f"font-weight: 600; padding: {SPACING['xs']}px 0;")
        layout.addWidget(summary_label)

        # Diff 视图
        prev_content = event_data.get("previous_content") or ""
        curr_content = event_data.get("current_content") or ""

        diff_text = QTextEdit()
        diff_text.setReadOnly(True)
        diff_text.setStyleSheet(f"font-family: {FONT_FAMILY_MONO}; font-size: {scaled_font_px("small")}px;")
        diff_html = self._build_diff_html(prev_content, curr_content)
        diff_text.setHtml(diff_html)
        layout.addWidget(diff_text)

        # 关闭按钮
        close_btn = QPushButton(_("关闭"))
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    @staticmethod
    def _build_diff_html(previous: str, current: str) -> str:
        """生成简单的删除/新增 diff HTML。"""
        prev_lines = previous.splitlines()
        curr_lines = current.splitlines()

        # 简单的行级差异
        prev_set = set(prev_lines)
        curr_set = set(curr_lines)

        result_parts: list[str] = ["<pre>"]
        # 语义色取自设计令牌（弹窗为瞬态，构建时取当前主题即可）
        tokens = ThemeManager.instance().tokens
        for line in prev_lines:
            if line not in curr_set and line.strip():
                result_parts.append(
                    f'<span style="background-color: {tokens.danger_bg}; color: {tokens.danger};">- {_escape_html(line)}</span>'
                )
        for line in curr_lines:
            if line not in prev_set and line.strip():
                result_parts.append(
                    f'<span style="background-color: {tokens.success_bg}; color: {tokens.success};">+ {_escape_html(line)}</span>'
                )
        result_parts.append("</pre>")
        return "\n".join(result_parts)


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


