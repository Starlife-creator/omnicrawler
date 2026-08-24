"""变更监控视图 — 定时抓取、哈希对比、变化通知的完整 GUI 面板。

提供:
    - ChangeMonitorView: 主标签页（规则列表 + 定时轮询）
    - NewRuleDialog: 新建/编辑规则对话框
    - ChangeEventDialog: 变化详情 diff 查看器

GUI 接入链路:
    - main.py 侧栏导航项（NavIndex.CHANGE_MONITOR=9）
    - 系统托盘通知（桌面弹窗）
    - 设置持久化规则列表
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QThread, QTimer, Signal, Slot
from PySide6.QtGui import QColor
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
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from omnicrawler.core.utils import user_agent
from omnicrawler.gui.widgets.toast import ToastManager

from ..i18n import _
from ..design_system import ThemeManager
from ..widgets.empty_state import EmptyState

if TYPE_CHECKING:
    from omnicrawler.gui.settings import AppSettings

LOGGER = logging.getLogger(__name__)

# ── 间隔选项 ────────────────────────────────────────────────────────

INTERVAL_OPTIONS = [
    (60, _("1 分钟")),
    (300, _("5 分钟")),
    (600, _("10 分钟")),
    (1800, _("30 分钟")),
    (3600, _("1 小时")),
    (7200, _("2 小时")),
    (21600, _("6 小时")),
    (43200, _("12 小时")),
    (86400, _("24 小时")),
]

CONDITION_OPTIONS = [
    ("changed", _("内容发生变化")),
    ("contains:{{text}}", _("包含指定文本")),
    ("regex:{{pattern}}", _("匹配正则表达式")),
    ("equals:{{value}}", _("精确等于某个值")),
]


# ── 后台检查线程 ────────────────────────────────────────────────────

class _CheckWorker(QThread):
    """后台执行 change_detector.check_all()，避免阻塞 GUI。"""

    finished = Signal(list)   # list[ChangeEvent]
    error = Signal(str)

    def __init__(
        self,
        rules_json: list[dict],
        parent: QWidget | None = None,
        *,
        fetcher: Any = None,
    ) -> None:
        super().__init__(parent)
        self._rules_json = rules_json
        self._fetcher = fetcher

    def run(self) -> None:
        import asyncio

        try:
            from omnicrawler.scheduling.change_detector import ChangeDetector, MonitorRule

            detector = ChangeDetector(fetcher=self._fetcher)
            for item in self._rules_json:
                detector.add_rule(MonitorRule.from_dict(item))

            events = asyncio.run(detector.check_all())
            self.finished.emit(events)
        except Exception as exc:
            self.error.emit(str(exc))


# ── 新建规则对话框 ──────────────────────────────────────────────────

class NewRuleDialog(QDialog):
    """新建或编辑监控规则。"""

    def __init__(
        self,
        parent: QWidget | None = None,
        rule_data: dict | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("新建变更监控规则") if rule_data is None else _("编辑变更监控规则"))
        self.setMinimumSize(480, 420)
        self._rule_data = rule_data

        layout = QFormLayout(self)
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

        notify = data.get("notify_methods", ["desktop"])
        self._notify_desktop.setChecked("desktop" in notify)
        self._notify_sound.setChecked("sound" in notify)

    def _on_condition_changed(self, idx: int) -> None:
        cond_key = self._condition_combo.itemData(idx)
        self._condition_value_edit.setVisible(cond_key != "changed")

    def _probe_url(self) -> None:
        url = self._url_edit.text().strip()
        if not url:
            QMessageBox.warning(self, _("提示"), _("请先输入目标 URL"))
            return
        # 简单异步探测
        import urllib.request

        try:
            req = urllib.request.Request(url, headers={"User-Agent": user_agent("Probe")})
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
                QMessageBox.information(self, _("探测成功"), _(f"URL 可达\nHTTP {resp.status}"))
        except Exception as exc:
            QMessageBox.warning(self, _("探测失败"), _(f"无法访问该 URL:\n{exc}"))

    def _test_selector(self) -> None:
        """测试 CSS 选择器在当前 URL 上是否匹配到内容。"""
        url = self._url_edit.text().strip()
        selector = self._selector_edit.text().strip()
        if not url:
            QMessageBox.warning(self, _("提示"), _("请先输入目标 URL"))
            return
        if not selector:
            QMessageBox.warning(self, _("提示"), _("请先输入 CSS 选择器"))
            return

        import urllib.request

        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) " +

                        "AppleWebKit/537.36 (KHTML, like Gecko) " +

                        "Chrome/127.0.0.0 Safari/537.36"
                    ),
                },
            )
            with urllib.request.urlopen(req, timeout=15) as resp:  # noqa: S310
                html = resp.read().decode(resp.headers.get_content_charset("utf-8"), errors="replace")

            from omnicrawler.scheduling.change_detector import ChangeDetector

            content = ChangeDetector._extract_content(html, selector)
            if content.strip():
                preview = content[:200] + ("..." if len(content) > 200 else "")
                QMessageBox.information(
                    self, _("选择器测试成功"),
                    _(f"选择器匹配到 {len(content)} 个字符\n\n预览:\n{preview}"),
                )
            else:
                QMessageBox.warning(self, _("选择器测试"), _("选择器未匹配到任何内容"))
        except Exception as exc:
            QMessageBox.warning(self, _("测试失败"), f"{exc}")

    def _validate_and_accept(self) -> None:
        url = self._url_edit.text().strip()
        name = self._name_edit.text().strip()

        if not name:
            name = url[:50] if url else _("未命名规则")
            self._name_edit.setText(name)

        if not url:
            QMessageBox.warning(self, _("提示"), _("请输入目标 URL"))
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
        summary_label.setStyleSheet("font-weight: bold; padding: 4px 0;")
        layout.addWidget(summary_label)

        # Diff 视图
        prev_content = event_data.get("previous_content") or ""
        curr_content = event_data.get("current_content") or ""

        diff_text = QTextEdit()
        diff_text.setReadOnly(True)
        diff_text.setStyleSheet("font-family: 'Consolas', 'Courier New', monospace; font-size: 12px;")
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


# ── 主标签页 ────────────────────────────────────────────────────────

class ChangeMonitorView(QWidget):
    """变更监控主视图 — 左侧导航栏的标签页。

    功能:
        - 规则列表（名称/URL/选择器/上次检查/状态）
        - 工具栏: 新建规则 / 全部检查 / 暂停监控
        - QTimer 定时触发后台检查
        - 桌面通知（通过系统托盘）

    GUI 链路:
        main.py → _setup_central_area → nav item 8 → ChangeMonitorView
    """

    # 信号：通知 main.py 弹出系统托盘消息
    desktop_notify = Signal(str, str)  # title, message

    def __init__(
        self,
        settings: AppSettings | None = None,
        parent: QWidget | None = None,
        *,
        fetcher: Any = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        # A3：可选共享 AsyncFetcher，检查时复用其连接池/EgressBroker 审计通道
        self._fetcher = fetcher
        self._rules_data: list[dict] = []
        self._paused: bool = False

        # 加载持久化规则
        if settings:
            self._rules_data = settings._value("monitor/rules", [], list)

        # ── 布局 ────────────────────────────────────────────────────
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 标题行
        title_row = QHBoxLayout()
        title = QLabel(_('<b style="font-size: 16px;">🔔 变更监控</b>'))
        title_row.addWidget(title)
        title_row.addStretch()

        self._status_label = QLabel(_("就绪"))
        self._status_token = "muted"  # 当前状态语义令牌名（info/warning/success/danger/muted）
        self._status_bold = False
        ThemeManager.instance().theme_changed.connect(self._apply_status_style)
        self._apply_status_style()
        title_row.addWidget(self._status_label)
        layout.addLayout(title_row)

        # 工具栏
        toolbar = QHBoxLayout()

        new_btn = QPushButton(_("+ 新建规则"))
        new_btn.clicked.connect(self._new_rule)
        toolbar.addWidget(new_btn)

        check_all_btn = QPushButton(_("▶ 全部检查"))
        check_all_btn.clicked.connect(self._check_all)
        toolbar.addWidget(check_all_btn)

        self._pause_btn = QPushButton(_("⏸ 暂停监控"))
        self._pause_btn.clicked.connect(self._toggle_pause)
        toolbar.addWidget(self._pause_btn)

        toolbar.addStretch()

        clear_btn = QPushButton(_("清空历史"))
        clear_btn.clicked.connect(self._clear_history)
        toolbar.addWidget(clear_btn)

        layout.addLayout(toolbar)

        # 规则列表（P3：空态统一 EmptyState，空态有主 CTA「+ 新建规则」）
        self._empty_state = EmptyState(
            icon="📡",
            title=_("暂无监控规则"),
            description=_("点击「+ 新建规则」创建第一条变化监测；之后每次变化会在这里列出。"),
            action_label=_("＋ 新建规则"),
            action_callback=self._new_rule,
        )
        self._rule_list = QListWidget()
        self._rule_list.setAlternatingRowColors(True)
        self._rule_list.setSpacing(2)
        layout.addWidget(self._empty_state)
        layout.addWidget(self._rule_list)

        # 底部状态
        bottom_row = QHBoxLayout()
        bottom_row.addWidget(QLabel(_("通知方式:")))
        self._notify_desktop_cb = QCheckBox(_("桌面通知"))
        self._notify_desktop_cb.setChecked(
            settings._value("monitor/desktop_notify", True, bool) if settings else True
        )
        self._notify_desktop_cb.toggled.connect(self._save_monitor_settings)
        bottom_row.addWidget(self._notify_desktop_cb)
        self._notify_sound_cb = QCheckBox(_("提示音"))
        self._notify_sound_cb.setChecked(
            settings._value("monitor/sound_notify", False, bool) if settings else False
        )
        self._notify_sound_cb.toggled.connect(self._save_monitor_settings)
        bottom_row.addWidget(self._notify_sound_cb)
        bottom_row.addStretch()
        layout.addLayout(bottom_row)

        # ── 定时器 ──────────────────────────────────────────────────
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._periodic_check)
        self._timer.start(30000)  # 每30秒轮询

        # ── 工作线程 ────────────────────────────────────────────────
        self._worker: _CheckWorker | None = None

        # ── 首次渲染 ────────────────────────────────────────────────
        self._refresh_list()

    # ── 规则管理 ────────────────────────────────────────────────────

    def _refresh_list(self) -> None:
        """刷新规则列表 UI。"""
        self._rule_list.clear()

        if not self._rules_data:
            # P3：空态统一 EmptyState（有主 CTA），隐藏空列表
            self._rule_list.hide()
            self._empty_state.show()
            return

        self._rule_list.show()
        self._empty_state.hide()
        for rule in self._rules_data:
            enabled = rule.get("enabled", True)
            name = rule.get("name", _("未命名"))
            url = rule.get("url", "")
            selector = rule.get("selector", "")
            last_checked = rule.get("last_checked", "")

            # 构建多行文本
            status_icon = "✓" if enabled else "✗"
            last_str = ""
            if isinstance(last_checked, str) and last_checked:
                try:
                    dt = datetime.fromisoformat(last_checked)
                    elapsed = (datetime.now(tz=timezone.utc) - dt).total_seconds()
                    if elapsed < 60:
                        last_str = _("刚才")
                    elif elapsed < 3600:
                        last_str = _(f"{int(elapsed / 60)} 分钟前")
                    elif elapsed < 86400:
                        last_str = _(f"{int(elapsed / 3600)} 小时前")
                    else:
                        last_str = _(f"{int(elapsed / 86400)} 天前")
                except ValueError:
                    last_str = last_checked[:19]

            lines = [
                f"{status_icon} {name}",
                f"   URL: {url[:80]}{'...' if len(url) > 80 else ''}",
                _(f"   选择器: {selector or '(整页)'}"),
                _(f"   上次检查: {last_str or '从未'}"),
            ]
            item = QListWidgetItem("\n".join(lines))
            if not enabled:
                item.setForeground(QColor(150, 150, 150))
            item.setData(1, rule.get("rule_id", ""))  # 存储 rule_id
            self._rule_list.addItem(item)

    def _new_rule(self) -> None:
        dialog = NewRuleDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            rule_data = dialog.get_rule_data()
            self._rules_data.append(rule_data)
            self._save_rules()
            self._refresh_list()
            ToastManager.instance().success(_(f"已添加监控规则: {rule_data['name']}"))

    def _edit_rule(self, rule_id: str) -> None:
        for i, r in enumerate(self._rules_data):
            if r.get("rule_id") == rule_id:
                dialog = NewRuleDialog(self, rule_data=r)
                if dialog.exec() == QDialog.DialogCode.Accepted:
                    updated = dialog.get_rule_data()
                    updated["rule_id"] = rule_id
                    self._rules_data[i] = updated
                    self._save_rules()
                    self._refresh_list()
                    ToastManager.instance().success(_(f"已更新规则: {updated['name']}"))
                return

    def _delete_rule(self, rule_id: str) -> None:
        for i, r in enumerate(self._rules_data):
            if r.get("rule_id") == rule_id:
                name = r.get("name", _("未命名"))
                reply = QMessageBox.question(
                    self, _("确认删除"), _(f"确定要删除监控规则「{name}」吗？"), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                )
                if reply == QMessageBox.StandardButton.Yes:
                    del self._rules_data[i]
                    self._save_rules()
                    self._refresh_list()
                    ToastManager.instance().info(_(f"已删除规则: {name}"))
                return

    def _toggle_rule(self, rule_id: str) -> None:
        for r in self._rules_data:
            if r.get("rule_id") == rule_id:
                r["enabled"] = not r.get("enabled", True)
                self._save_rules()
                self._refresh_list()
                return

    # ── 检查执行 ────────────────────────────────────────────────────

    def _set_status_style(self, token: str, *, bold: bool = False) -> None:
        """按设计令牌设置状态标签颜色；主题切换时自动跟随刷新。"""
        self._status_token = token
        self._status_bold = bold
        self._apply_status_style()

    def _apply_status_style(self, *_args: Any) -> None:
        """应用当前状态令牌色（theme_changed 信号复用入口）。"""
        tokens = ThemeManager.instance().tokens
        color = getattr(tokens, self._status_token, tokens.muted)
        style = f"color: {color};"
        if self._status_bold:
            style += " font-weight: bold;"
        self._status_label.setStyleSheet(style)

    def _check_all(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            ToastManager.instance().warning(_("检查仍在进行中，请稍候"))
            return
        if not self._rules_data:
            ToastManager.instance().info(_("没有可检查的规则"))
            return

        self._status_label.setText(_("检查中..."))
        self._set_status_style("info")

        self._worker = _CheckWorker(self._rules_data, self, fetcher=self._fetcher)
        self._worker.finished.connect(self._on_check_finished)
        self._worker.error.connect(self._on_check_error)
        self._worker.start()

    def _periodic_check(self) -> None:
        """定时器触发的后台检查。"""
        if self._paused or not self._rules_data or self._worker is not None:
            return

        # 检查是否有到期的规则
        now = datetime.now(tz=timezone.utc)
        due = False
        for rule in self._rules_data:
            if not rule.get("enabled", True):
                continue
            last_str = rule.get("last_checked", "")
            if not last_str:
                due = True
                break
            try:
                last = datetime.fromisoformat(last_str)
                interval = rule.get("check_interval", 3600)
                if (now - last).total_seconds() >= interval:
                    due = True
                    break
            except (ValueError, TypeError):
                due = True
                break

        if due:
            self._check_all()

    @Slot(list)
    def _on_check_finished(self, events: list) -> None:
        self._worker = None
        events_list = list(events)

        if events_list:
            self._status_label.setText(_(f"检测到 {len(events_list)} 个变化"))
            self._set_status_style("warning", bold=True)

            # 更新 rules_data 中的 last_hash/last_checked
            for event in events_list:
                ed = event.to_dict()
                for rule in self._rules_data:
                    if rule.get("rule_id") == ed["rule_id"]:
                        rule["last_hash"] = ed["current_hash"]
                        rule["last_checked"] = ed["detected_at"]
                        # 桌面通知
                        if self._notify_desktop_cb.isChecked():
                            self.desktop_notify.emit(
                                _(f"变更监控: {ed['rule_name']}"),
                                ed.get("diff_summary", _("检测到变化")),
                            )
                        break

            self._save_rules()
            self._refresh_list()

            # 弹出详情
            first_event = events_list[0]
            self._show_event_detail(first_event.to_dict())
        else:
            self._status_label.setText(_("无变化"))
            self._set_status_style("success")
            # 更新检查时间（S3.2.1：不再写 "__baseline__" 哨兵假哈希——
            # 基线由 ChangeDetector 内部持久化，每轮比较真实哈希）
            now = datetime.now(tz=timezone.utc).isoformat()
            for rule in self._rules_data:
                rule["last_checked"] = now
            self._save_rules()
            self._refresh_list()

    @Slot(str)
    def _on_check_error(self, error: str) -> None:
        self._worker = None
        self._status_label.setText(_("检查失败"))
        self._set_status_style("danger")
        ToastManager.instance().error(_(f"变更检查失败: {error}"))

    def _show_event_detail(self, event_data: dict) -> None:
        dialog = ChangeEventDialog(event_data, self)
        dialog.exec()

    # ── 暂停 / 继续 ────────────────────────────────────────────────

    def _toggle_pause(self) -> None:
        self._paused = not self._paused
        if self._paused:
            self._pause_btn.setText(_("▶ 继续监控"))
            self._status_label.setText(_("已暂停"))
            self._status_label.setStyleSheet("color: gray;")
        else:
            self._pause_btn.setText(_("⏸ 暂停监控"))
            self._status_label.setText(_("就绪"))
            self._status_label.setStyleSheet("color: gray;")

    # ── 持久化 ────────────────────────────────────────────────────

    def _save_rules(self) -> None:
        """保存规则到设置（S3.2.1 ⑥：失败可见，不再静默丢失）。"""
        if self._settings:
            serializable = []
            for r in self._rules_data:
                d = dict(r)
                for key in ("last_checked", "created_at"):
                    val = d.get(key)
                    if isinstance(val, datetime):
                        d[key] = val.isoformat()
                    elif val is None and key == "created_at":
                        d[key] = datetime.now(tz=timezone.utc).isoformat()
                serializable.append(d)
            try:
                self._settings.set_value("monitor/rules", serializable)
            except Exception as exc:  # noqa: BLE001 - 写失败提示不崩溃
                ToastManager.instance().error(_(f"监控规则保存失败: {exc}"))

    def _save_monitor_settings(self) -> None:
        if self._settings:
            self._settings._set_value("monitor/desktop_notify", self._notify_desktop_cb.isChecked())
            self._settings._set_value("monitor/sound_notify", self._notify_sound_cb.isChecked())

    def _clear_history(self) -> None:
        reply = QMessageBox.question(
            self, _("确认清空"),
            _("这会清除所有规则的检测历史（保留规则定义），确定吗？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            for rule in self._rules_data:
                rule["last_hash"] = None
                rule["last_checked"] = None
            self._save_rules()
            self._refresh_list()
            ToastManager.instance().info(_("已清空检测历史"))

    # ── 右键菜单 ────────────────────────────────────────────────────

    def contextMenuEvent(self, event) -> None:
        viewport = self._rule_list.viewport()
        if viewport is None:
            return
        item = self._rule_list.itemAt(viewport.mapFromGlobal(event.globalPos()))
        if item is None:
            return
        rule_id = item.data(1)
        if not rule_id:
            return

        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        menu.addAction(_("编辑"), lambda: self._edit_rule(rule_id))
        menu.addAction(_("立即检查"), lambda: self._check_single(rule_id))

        # 找到规则状态
        for r in self._rules_data:
            if r.get("rule_id") == rule_id:
                toggle_text = _("禁用") if r.get("enabled", True) else _("启用")
                menu.addAction(toggle_text, lambda: self._toggle_rule(rule_id))
                break

        menu.addSeparator()
        menu.addAction(_("删除"), lambda: self._delete_rule(rule_id))
        menu.exec(event.globalPos())

    def _check_single(self, rule_id: str) -> None:
        """手动检查单条规则。"""
        for rule in self._rules_data:
            if rule.get("rule_id") == rule_id:
                self._status_label.setText(_("检查中..."))
                self._set_status_style("info")
                self._worker = _CheckWorker([rule], self, fetcher=self._fetcher)
                self._worker.finished.connect(self._on_check_finished)
                self._worker.error.connect(self._on_check_error)
                self._worker.start()
                return
