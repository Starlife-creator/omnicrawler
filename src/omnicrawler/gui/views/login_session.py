"""「登录会话」独立导航页（《优化方案》§11.1 U3 · 三项裁定全部落地）。

页面职责（只做编排，不碰凭据）

* 登录区：站点地址 + 账户 + 「打开登录窗口」（headed Playwright，`U1`）；
* 同步开关：登录后是否把该站点 cookie 同步给 HTTP 引擎（`U2` 的桥），默认开；
  **首次进入本页弹一次性提醒**（本地标记 `session.notice_ack_v1`）；
* 会话列表：账户 / 域名 / cookie 数 / 时间 / 删除；
* 计时与两个操作：「保存并关闭」立即收尾、「延长 15 分钟」可反复点；
  到点**先保存再关闭**（收尾幂等单入口在 `LoginSessionManager.finalize`）。

★ 凭据边界：本页只展示**元数据**（账户、域名、cookie 计数、路径），
从不读取或显示 cookie 值；桥接失败时提示里也只有阶段的结论。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.config import AppConfig
from ...core.errors import PolicyBlockedError
from ...fetching.login_session import (
    LoginLauncher,
    LoginPhase,
    LoginSessionError,
    LoginSessionManager,
    LoginSessionSnapshot,
    playwright_available,
)
from ...fetching.session_bridge import (
    SessionBridgeError,
    bridge_from_storage_state_file,
)
from ...fetching.session_state import (
    SESSIONS_DIRNAME,
    SessionPersistenceDisabledError,
    SessionSummary,
    list_sessions,
    remove_session,
)
from ..design_system import SPACING
from ..i18n import _
from ..settings import AppSettings
from ..widgets.empty_state import EmptyState
from ..widgets.toast import ToastManager
from .login_session_logic import (
    BRIDGE_OVERRIDE_SETTING,
    NOTICE_ACK_KEY,
    bridge_hosts,
    degradation_hint,
    effective_bridge_enabled,
    format_remaining,
    notice_text,
    phase_label,
    session_row,
)

_ACTIVE_PHASES = frozenset({LoginPhase.LAUNCHING, LoginPhase.WAITING_LOGIN})
_SETTING_TRUE = "1"
_SETTING_FALSE = "0"


class LoginSessionView(QWidget):
    """登录会话页（工具分组）。

    ``config_provider`` 而不是"构造时传一个 config 对象"：主窗口换项目/换配置时会
    **替换** ``mw._config``。若本页缓存旧对象，会话列表与删除动作会落到**上一个
    工作区**的 ``sessions/`` 目录上 —— 那是会误删别人东西的错误，必须由结构挡住。
    """

    def __init__(
        self,
        config_provider: Callable[[], AppConfig],
        *,
        settings: AppSettings | None = None,
        launcher: LoginLauncher | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._config_provider = config_provider
        self._settings = settings if settings is not None else AppSettings.instance()
        self._launcher = launcher
        self._config = config_provider()
        self._manager = LoginSessionManager(self._config, launcher=launcher)
        self._sessions: tuple[SessionSummary, ...] = ()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._on_tick)
        self._build_ui()
        self.setAccessibleName(_("登录会话"))
        self.setAccessibleDescription(_("管理目标站点的登录会话：打开登录窗口、查看与删除已保存的会话。"))
        self.refresh()

    # ── 构造 ──────────────────────────────────────────────
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(SPACING["lg"], SPACING["lg"], SPACING["lg"], SPACING["lg"])
        layout.setSpacing(SPACING["md"])

        title = QLabel(_("登录会话"))
        title.setObjectName("pageTitle")
        layout.addWidget(title)

        self._hint_label = QLabel("")
        self._hint_label.setWordWrap(True)
        self._hint_label.setProperty("status", "warning")
        self._hint_label.setAccessibleName(_("不可用提示"))
        layout.addWidget(self._hint_label)

        layout.addWidget(self._build_login_box())
        layout.addWidget(self._build_status_box())
        layout.addWidget(self._build_sessions_box())
        layout.addStretch(1)

    def _build_login_box(self) -> QGroupBox:
        box = QGroupBox(_("打开登录窗口"))
        box.setAccessibleName(_("登录区"))
        form = QVBoxLayout(box)
        form.setSpacing(SPACING["sm"])

        url_row = QHBoxLayout()
        url_row.addWidget(QLabel(_("站点地址")))
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText(_("https://example.com/login"))
        self._url_edit.setAccessibleName(_("要登录的站点地址"))
        url_row.addWidget(self._url_edit, 1)
        form.addLayout(url_row)

        account_row = QHBoxLayout()
        account_row.addWidget(QLabel(_("账户")))
        self._account_edit = QLineEdit()
        self._account_edit.setPlaceholderText(_("留空表示使用任务配置里的会话名"))
        self._account_edit.setAccessibleName(_("登录账户标识"))
        account_row.addWidget(self._account_edit, 1)
        form.addLayout(account_row)

        actions = QHBoxLayout()
        self._open_button = QPushButton(_("打开登录窗口"))
        self._open_button.clicked.connect(self._on_open_login_window)
        actions.addWidget(self._open_button)
        self._bridge_checkbox = QCheckBox(_("登录后同步给 HTTP 引擎"))
        self._bridge_checkbox.setAccessibleName(_("登录态同步开关"))
        self._bridge_checkbox.toggled.connect(self._on_bridge_toggled)
        actions.addWidget(self._bridge_checkbox)
        actions.addStretch(1)
        form.addLayout(actions)

        note = QLabel(
            _("窗口只用于你本人手动登录：程序不代填密码、不代过验证码，也不读取 cookie 内容。")
        )
        note.setWordWrap(True)
        note.setObjectName("muted")
        form.addWidget(note)
        return box

    def _build_status_box(self) -> QGroupBox:
        box = QGroupBox(_("当前会话"))
        box.setAccessibleName(_("登录状态区"))
        row = QVBoxLayout(box)
        row.setSpacing(SPACING["sm"])

        self._phase_label = QLabel("")
        self._phase_label.setAccessibleName(_("登录状态"))
        row.addWidget(self._phase_label)

        self._remaining_label = QLabel("")
        row.addWidget(self._remaining_label)

        self._detail_label = QLabel("")
        self._detail_label.setWordWrap(True)
        self._detail_label.setObjectName("muted")
        row.addWidget(self._detail_label)

        buttons = QHBoxLayout()
        self._save_button = QPushButton(_("保存并关闭"))
        self._save_button.clicked.connect(self._on_save_and_close)
        buttons.addWidget(self._save_button)
        self._extend_button = QPushButton(_("延长 15 分钟"))
        self._extend_button.clicked.connect(self._on_extend)
        buttons.addWidget(self._extend_button)
        buttons.addStretch(1)
        row.addLayout(buttons)
        return box

    def _build_sessions_box(self) -> QGroupBox:
        box = QGroupBox(_("已保存的登录会话"))
        box.setAccessibleName(_("会话列表区"))
        layout = QVBoxLayout(box)
        layout.setSpacing(SPACING["sm"])

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels([_("账户"), _("域名"), _("cookie 数"), _("时间")])
        self._table.setAccessibleName(_("已保存的登录会话列表"))
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.verticalHeader().setVisible(False)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self._table.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self._table)

        self._empty = EmptyState(
            icon="🔐",
            title=_("还没有保存的登录会话"),
            description=_("在上面填写站点地址并打开登录窗口，完成手动登录后会话会出现在这里。"),
        )
        layout.addWidget(self._empty)

        actions = QHBoxLayout()
        self._delete_button = QPushButton(_("删除选中会话"))
        self._delete_button.clicked.connect(self._on_delete_selected)
        self._delete_button.setEnabled(False)
        actions.addWidget(self._delete_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        return box

    # ── 对外接口 ──────────────────────────────────────────
    def activate(self) -> None:
        """进入本页时调用（导航分派）：刷新 + 首次进入的一次性提醒（裁定 1）。"""
        self.refresh()
        if self._needs_notice():
            self._show_first_run_notice()

    def refresh(self) -> None:
        self._sync_config()
        self._refresh_hint()
        self._refresh_bridge_switch()
        self._refresh_status()
        self._refresh_sessions()

    def prefill(self, *, account: str = "", url: str = "") -> None:
        """预填登录区（U4 的「一键跳转登录页」用）。"""
        if account:
            self._account_edit.setText(account)
        if url:
            self._url_edit.setText(url)

    @property
    def manager(self) -> LoginSessionManager:
        return self._manager

    # ── 刷新 ──────────────────────────────────────────────
    def _sync_config(self) -> None:
        """跟随主窗口的配置对象；换了配置就重建管理器并停表（不跨工作区延续）。

        ★ **进行中的会话期间不接受新配置**：关窗事件与超时收尾都走管理器，
        换掉它等于把正在等待的登录窗口丢给"没人收尾"（窗口还开着，页面却不认）。
        """
        if self._manager.phase in _ACTIVE_PHASES:
            return
        current = self._config_provider()
        if current is self._config:
            return
        self._config = current
        self._timer.stop()
        self._manager = LoginSessionManager(current, launcher=self._launcher)

    def _refresh_hint(self) -> None:
        hint = degradation_hint(
            playwright_installed=self._playwright_ready(),
            persistence_enabled=self._persistence_enabled(),
        )
        self._hint_label.setText(hint)
        self._hint_label.setVisible(bool(hint))
        self._open_button.setEnabled(not hint)

    def _refresh_bridge_switch(self) -> None:
        self._bridge_checkbox.blockSignals(True)
        try:
            self._bridge_checkbox.setChecked(
                effective_bridge_enabled(self._config, self._bridge_override())
            )
        finally:
            self._bridge_checkbox.blockSignals(False)

    def _refresh_status(self) -> None:
        snapshot = self._manager.snapshot()
        active = snapshot.phase in _ACTIVE_PHASES
        self._phase_label.setText(phase_label(snapshot.phase))
        self._remaining_label.setVisible(active)
        if active:
            remaining = format_remaining(snapshot.remaining_seconds)
            self._remaining_label.setText(_("剩余 {0}").format(remaining))
        self._detail_label.setText(snapshot.message)
        self._save_button.setEnabled(active)
        self._extend_button.setEnabled(active)

    def _refresh_sessions(self) -> None:
        self._sessions = list_sessions(self._config.workspace)
        self._table.setRowCount(len(self._sessions))
        for row, summary in enumerate(self._sessions):
            for column, text in enumerate(session_row(summary)):
                self._table.setItem(row, column, QTableWidgetItem(text))
        self._table.setVisible(bool(self._sessions))
        self._empty.setVisible(not self._sessions)
        self._delete_button.setEnabled(False)

    def _on_selection_changed(self) -> None:
        self._delete_button.setEnabled(self._table.currentRow() >= 0)

    # ── 交互 ──────────────────────────────────────────────
    def _on_open_login_window(self) -> None:
        self._sync_config()
        if not self._persistence_enabled():
            ToastManager.instance().warning(
                _("未开启 session.persist_cookies，登录态无处保存，已取消打开窗口。")
            )
            return
        url = self._url_edit.text().strip()
        if not url:
            ToastManager.instance().warning(_("请先填写要登录的站点地址。"))
            return
        proxy = str(self._config.section("http").get("proxy", ""))
        try:
            self._manager.begin(
                url, account=self._account_edit.text().strip(), proxy=proxy
            )
        except (LoginSessionError, SessionPersistenceDisabledError, PolicyBlockedError) as exc:
            ToastManager.instance().error(_("无法打开登录窗口：{0}").format(exc))
            self.refresh()
            return
        self._timer.start()
        self.refresh()

    def _on_save_and_close(self) -> None:
        snapshot = self._manager.finalize(reason="save_and_close")
        self._on_session_finished(snapshot)

    def _on_extend(self) -> None:
        try:
            self._manager.extend()
        except LoginSessionError as exc:
            ToastManager.instance().warning(str(exc))
        self.refresh()

    def _on_tick(self) -> None:
        snapshot = self._manager.poll()
        if snapshot is None:
            self._timer.stop()
            self.refresh()
            return
        if snapshot.phase in _ACTIVE_PHASES:
            # 等待期间每秒只刷新**状态区**：列表刷新要重读并解析全部快照，
            # 在这里每秒做一次纯属浪费，且会在用户选行时把选中项清掉。
            self._refresh_status()
            return
        self._timer.stop()
        self._on_session_finished(snapshot)

    def _on_session_finished(self, snapshot: LoginSessionSnapshot) -> None:
        """收尾后的统一处理：先如实报告"存了没有"，再谈同步。"""
        self._timer.stop()
        self.refresh()
        if snapshot.phase is not LoginPhase.SAVED:
            return
        if not snapshot.state_path:
            return
        if not self._bridge_checkbox.isChecked():
            ToastManager.instance().success(_("登录态已保存（本次未同步给 HTTP 引擎）。"))
            return
        self._bridge_to_http(snapshot)

    def _bridge_to_http(self, snapshot: LoginSessionSnapshot) -> None:
        hosts = bridge_hosts(self._config, login_url=snapshot.login_url)
        try:
            result = bridge_from_storage_state_file(
                self._config, Path(snapshot.state_path), hosts=hosts
            )
        except (SessionBridgeError, SessionPersistenceDisabledError) as exc:
            # ★ 保存本身是成功的，不能被"同步失败"说成整体失败。
            ToastManager.instance().warning(
                _("登录态已保存，但同步给 HTTP 引擎失败：{0}").format(exc)
            )
            return
        ToastManager.instance().success(
            _("登录态已同步给 HTTP 引擎（{0} 条 cookie，{1} 个域名）。").format(
                result.added, len(result.domains)
            )
        )

    def _on_delete_selected(self) -> None:
        row = self._table.currentRow()
        if row < 0 or row >= len(self._sessions):
            ToastManager.instance().warning(_("请先选中要删除的会话。"))
            return
        summary = self._sessions[row]
        answer = QMessageBox.question(
            self,
            _("删除登录会话"),
            _("确定删除账户「{0}」的登录会话快照吗？下次采集需要重新登录。").format(
                summary.account
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            remove_session(summary.path, workspace=self._config.workspace)
        except (OSError, ValueError) as exc:
            ToastManager.instance().error(_("删除失败：{0}").format(exc))
            return
        ToastManager.instance().success(_("已删除登录会话。"))
        self.refresh()

    def _on_bridge_toggled(self, checked: bool) -> None:
        self._settings.set_value(
            BRIDGE_OVERRIDE_SETTING, _SETTING_TRUE if checked else _SETTING_FALSE
        )

    # ── 辅助 ──────────────────────────────────────────────
    def _persistence_enabled(self) -> bool:
        return bool(self._config.section("session").get("persist_cookies", False))

    def _playwright_ready(self) -> bool:
        return self._launcher is not None or playwright_available()

    def _bridge_override(self) -> bool | None:
        raw = str(self._settings.value(BRIDGE_OVERRIDE_SETTING, "", str) or "")
        if raw == _SETTING_TRUE:
            return True
        if raw == _SETTING_FALSE:
            return False
        return None

    def _needs_notice(self) -> bool:
        return not bool(self._settings.value(NOTICE_ACK_KEY, False, bool))

    def _show_first_run_notice(self) -> None:
        sessions_dir = str(self._config.workspace / SESSIONS_DIRNAME)
        QMessageBox.information(
            self,
            _("登录会话说明（只提示一次）"),
            notice_text(sessions_dir=sessions_dir, bridge_enabled=self._bridge_checkbox.isChecked()),
        )
        self._settings.set_value(NOTICE_ACK_KEY, True)

    def stop_timer(self) -> None:
        """窗口关闭等场景下停掉轮询定时器（幂等）。"""
        self._timer.stop()
