"""市场页的「私人」与「本地」栏目 + 上传市场对话框。

- 私人栏：项目 plugins/ 下由当前身份签名的插件（及其它未签名目录，
  可在此一键签名）；模板同理（templates/ 目录）。
- 本地栏：plugins/ 下的其它条目（未签名 / 已签名未信任 / 已签名已信任），
  未信任条目可在此点击信任后加载。
- 上传市场：把私人栏条目打包为 PR 提交到维护者市场仓库（gh CLI）。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...plugins.identity import IdentityStore
from ...plugins.market_uploader import create_market_pr, pr_body
from ...plugins.plugin_packaging import (
    LocalPluginEntry,
    build_plugin_upload,
    build_template_upload,
    scan_local_plugins,
    sign_plugin_local,
)
from ...plugins.trust import CreatorIdentity, TrustedUserList
from ..core.background_worker import BackgroundWorker
from ..i18n import _
from ..widgets.toast import ToastManager

LOGGER = logging.getLogger(__name__)

_STATUS_TEXT = {
    "signed_by_me": _("私人（已签名）"),
    "signed_trusted": _("已信任"),
    "signed_untrusted": _("未信任"),
    "unsigned": _("未签名"),
}


class _SignWorker(BackgroundWorker):
    """后台执行本地签名（creator-sign + 自动信任）。"""

    def __init__(self, plugin_dir: Path, username: str, password: str, target: str, parent=None) -> None:
        super().__init__(parent)
        self._plugin_dir = plugin_dir
        self._username = username
        self._password = password
        self._target = target

    def work(self) -> str:
        return sign_plugin_local(
            self._plugin_dir, username=self._username, password=self._password, target=self._target
        )


class _UploadWorker(BackgroundWorker):
    """后台生成上传包并提交 PR。"""

    def __init__(
        self, payload_kind: str, payload: dict[str, bytes], title: str, body: str, parent=None
    ) -> None:
        super().__init__(parent)
        self._payload_kind = payload_kind
        self._payload = payload
        self._title = title
        self._body = body

    def work(self) -> str:
        return create_market_pr(files=self._payload, title=self._title, body=self._body)


def _status_badge(entry: LocalPluginEntry) -> str:
    text = _STATUS_TEXT.get(entry.status, entry.status)
    if entry.status == "signed_untrusted" and entry.author_username:
        return _(f"{text} · 作者 {entry.author_username}")
    return text


class _LocalPluginsPane(QWidget):
    """私人/本地共用插件列表（kind: private | local）。"""

    def __init__(self, root: Path, kind: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._root = root
        self._kind = kind
        self._entries: list[LocalPluginEntry] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(10)

        top = QHBoxLayout()
        hint = QLabel(
            _("项目 plugins/ 目录中的插件。复制他人插件文件夹粘贴到此处即可出现在列表。")
            if self._kind == "local"
            else _("自制插件放入项目 plugins/ 目录后在此签名；签名后即可上传市场。")
        )
        hint.setObjectName("mutedLabel")
        hint.setWordWrap(True)
        top.addWidget(hint, 1)
        self._refresh_btn = QPushButton(_("刷新"))
        self._refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self._refresh_btn)
        root_layout.addLayout(top)

        splitter = QSplitter()
        splitter.setOrientation(Qt.Orientation.Horizontal)

        list_panel = QFrame()
        list_panel.setProperty("card", True)
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(10, 10, 10, 10)
        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_selection)
        list_layout.addWidget(self._list)
        splitter.addWidget(list_panel)

        detail_panel = QFrame()
        detail_panel.setProperty("card", True)
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(12, 12, 12, 12)
        self._detail_name = QLabel(_("未选择"))
        self._detail_name.setObjectName("detailTitle")
        detail_layout.addWidget(self._detail_name)
        self._detail_meta = QLabel("")
        self._detail_meta.setObjectName("mutedLabel")
        self._detail_meta.setWordWrap(True)
        detail_layout.addWidget(self._detail_meta)
        self._detail_desc = QLabel("")
        self._detail_desc.setWordWrap(True)
        detail_layout.addWidget(self._detail_desc, 1)

        buttons = QHBoxLayout()
        self._sign_btn = QPushButton(_("签名"))
        self._sign_btn.setProperty("primary", True)
        self._sign_btn.clicked.connect(self._on_sign)
        buttons.addWidget(self._sign_btn)
        self._upload_btn = QPushButton(_("上传市场"))
        self._upload_btn.clicked.connect(self._on_upload)
        buttons.addWidget(self._upload_btn)
        self._trust_btn = QPushButton(_("信任该作者"))
        self._trust_btn.clicked.connect(self._on_trust)
        buttons.addWidget(self._trust_btn)
        buttons.addStretch(1)
        detail_layout.addLayout(buttons)
        splitter.addWidget(detail_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        root_layout.addWidget(splitter, 1)

        self._footer = QLabel("")
        self._footer.setObjectName("mutedLabel")
        self._footer.setWordWrap(True)
        root_layout.addWidget(self._footer)

        self._update_buttons()

    # ── 列表 ─────────────────────────────────────────────────
    def refresh(self) -> None:
        self._list.clear()
        self._entries = [
            entry
            for entry in scan_local_plugins(self._root)
            if (entry.status == "signed_by_me") == (self._kind == "private")
        ]
        for entry in self._entries:
            item = QListWidgetItem(_(f"{entry.name} @ {entry.version}  ·  {_status_badge(entry)}"))
            item.setData(Qt.ItemDataRole.UserRole, entry)
            item.setToolTip(entry.description or entry.path.name)
            self._list.addItem(item)
        self._footer.setText(_(f"共 {len(self._entries)} 个条目"))

    def _current(self) -> LocalPluginEntry | None:
        item = self._list.currentItem()
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _on_selection(self, _current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        entry = self._current()
        if entry is None:
            self._detail_name.setText(_("未选择"))
            self._detail_meta.setText("")
            self._detail_desc.setText("")
        else:
            self._detail_name.setText(_(f"{entry.name} @ {entry.version}"))
            self._detail_meta.setText(_(f"状态：{_status_badge(entry)}"))
            self._detail_desc.setText(entry.description or _("（无介绍）"))
        self._update_buttons()

    def _update_buttons(self) -> None:
        entry = self._current()
        status = entry.status if entry is not None else "none"
        self._sign_btn.setVisible(self._kind == "private" and status == "unsigned")
        self._upload_btn.setVisible(self._kind == "private" and status == "signed_by_me")
        self._trust_btn.setVisible(self._kind == "local" and status == "signed_untrusted")

    # ── 操作 ─────────────────────────────────────────────────
    def _ask_password(self) -> tuple[str, str] | None:
        usernames = IdentityStore().list_usernames()
        if not usernames:
            ToastManager.instance().warning(_("请先创建身份：市场页顶部「身份与信任」"))
            return None
        dialog = QDialog(self)
        dialog.setWindowTitle(_("身份签名"))
        layout = QFormLayout(dialog)
        username_edit = QLineEdit(dialog)
        username_edit.setPlaceholderText(_(f"可用身份：{', '.join(usernames)}"))
        password_edit = QLineEdit(dialog)
        password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow(_("用户名"), username_edit)
        layout.addRow(_("密码"), password_edit)
        buttons = QHBoxLayout()
        ok_btn = QPushButton(_("确定"), dialog)
        cancel_btn = QPushButton(_("取消"), dialog)
        buttons.addStretch(1)
        buttons.addWidget(ok_btn)
        buttons.addWidget(cancel_btn)
        layout.addRow(buttons)
        ok_btn.clicked.connect(dialog.accept)
        cancel_btn.clicked.connect(dialog.reject)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        username = username_edit.text().strip()
        password = password_edit.text()
        if not username or not password:
            ToastManager.instance().warning(_("用户名与密码不能为空"))
            return None
        return username, password

    def _on_sign(self) -> None:
        entry = self._current()
        if entry is None:
            return
        credentials = self._ask_password()
        if credentials is None:
            return
        username, password = credentials
        self._sign_btn.setEnabled(False)
        worker = _SignWorker(entry.path, username, password, "plugin.py", parent=self)

        def _done(fingerprint: str) -> None:
            ToastManager.instance().success(_(f"已签名（指纹 {fingerprint}），作者自动加入信任列表"))
            self.refresh()

        def _failed(message: str) -> None:
            ToastManager.instance().error(_(f"签名失败：{message.splitlines()[0]}"))
            self._sign_btn.setEnabled(True)

        worker.succeeded.connect(_done)
        worker.failed.connect(_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_upload(self) -> None:
        entry = self._current()
        if entry is None:
            return
        credentials = self._ask_password()
        if credentials is None:
            return
        username, password = credentials
        dialog = UploadMarketDialog(self, kind="plugin", defaults={}, username=username)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        listing = dialog.listing()
        self._upload_btn.setEnabled(False)
        try:
            payload = build_plugin_upload(entry.path, username=username, password=password, listing=listing)
        except Exception as exc:  # noqa: BLE001
            ToastManager.instance().error(_(f"打包失败：{exc}"))
            self._upload_btn.setEnabled(True)
            return
        plugin_id = entry.path.name
        worker = _UploadWorker(
            "plugin",
            payload,
            title=_(f"提交插件 {plugin_id}（{username}）"),
            body=pr_body(_("插件"), plugin_id, username),
            parent=self,
        )

        def _done(url: str) -> None:
            ToastManager.instance().success(_(f"PR 已创建：{url}"))
            self._upload_btn.setEnabled(True)

        def _failed(message: str) -> None:
            ToastManager.instance().error(_(f"上传失败：{message.splitlines()[0]}"))
            self._upload_btn.setEnabled(True)

        worker.succeeded.connect(_done)
        worker.failed.connect(_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_trust(self) -> None:
        entry = self._current()
        if entry is None or not entry.author_username or not entry.fingerprint:
            return
        reply = QMessageBox.question(
            self,
            _("信任作者"),
            _("插件 {0} 由 {1}（指纹 {2}）签名。信任后该作者的全部插件将自动加载。").format(
                entry.name, entry.author_username, entry.fingerprint
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        creator = CreatorIdentity(
            username=entry.author_username, public_key=b"", key_fingerprint=entry.fingerprint
        )
        if TrustedUserList().add(creator, source="manual", path_hint=f"（{entry.name}）"):
            ToastManager.instance().success(_(f"已信任作者 {entry.author_username}"))
        else:
            ToastManager.instance().info(_(f"作者 {entry.author_username} 已在信任列表"))
        self.refresh()


class _LocalTemplatesPane(QWidget):
    """私人模板栏：项目 templates/ 目录下的 template.yaml 可签名、上传。"""

    def __init__(self, root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._root = root
        self._templates: list[Path] = []
        self._build_ui()
        self.refresh()

    def _build_ui(self) -> None:
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(10)

        hint = QLabel(
            _("自制模板：把 template.yaml 放入项目根 templates/ 目录（每个子目录一份），在此签名后上传市场。")
        )
        hint.setObjectName("mutedLabel")
        hint.setWordWrap(True)
        root_layout.addWidget(hint)

        self._list = QListWidget()
        self._list.currentItemChanged.connect(self._on_selection)
        root_layout.addWidget(self._list, 1)

        self._meta = QLabel(_("未选择"))
        self._meta.setObjectName("mutedLabel")
        self._meta.setWordWrap(True)
        root_layout.addWidget(self._meta)

        buttons = QHBoxLayout()
        self._sign_btn = QPushButton(_("签名"))
        self._sign_btn.setProperty("primary", True)
        self._sign_btn.clicked.connect(self._on_sign)
        buttons.addWidget(self._sign_btn)
        self._upload_btn = QPushButton(_("上传市场"))
        self._upload_btn.clicked.connect(self._on_upload)
        buttons.addWidget(self._upload_btn)
        buttons.addStretch(1)
        root_layout.addLayout(buttons)

        refresh_btn = QPushButton(_("刷新"))
        refresh_btn.clicked.connect(self.refresh)
        buttons.addWidget(refresh_btn)

        self._update_buttons()

    def refresh(self) -> None:
        self._list.clear()
        self._templates = []
        base = self._root / "templates"
        if base.is_dir():
            for template_dir in sorted(base.iterdir()):
                if template_dir.is_dir() and (template_dir / "template.yaml").is_file():
                    self._templates.append(template_dir)
        for template_dir in self._templates:
            signed = (template_dir / "creator.identity").is_file()
            self._list.addItem(_(f"{template_dir.name}  ·  {'已签名' if signed else '未签名'}"))
        self._meta.setText(_(f"共 {len(self._templates)} 个模板"))

    def _current(self) -> Path | None:
        item = self._list.currentItem()
        if item is None:
            return None
        return self._templates[self._list.row(item)]

    def _on_selection(self, *_args: Any) -> None:
        template_dir = self._current()
        if template_dir is None:
            self._meta.setText(_("未选择"))
        else:
            signed = (template_dir / "creator.identity").is_file()
            self._meta.setText(_(f"{template_dir.name}：{'已签名' if signed else '未签名'}"))
        self._update_buttons()

    def _update_buttons(self) -> None:
        template_dir = self._current()
        signed = template_dir is not None and (template_dir / "creator.identity").is_file()
        self._sign_btn.setVisible(not signed)
        self._upload_btn.setVisible(signed)

    def _ask_password(self) -> tuple[str, str] | None:
        usernames = IdentityStore().list_usernames()
        if not usernames:
            ToastManager.instance().warning(_("请先创建身份：市场页顶部「身份与信任」"))
            return None
        dialog = QDialog(self)
        dialog.setWindowTitle(_("身份签名"))
        layout = QFormLayout(dialog)
        username_edit = QLineEdit(dialog)
        password_edit = QLineEdit(dialog)
        password_edit.setEchoMode(QLineEdit.EchoMode.Password)
        layout.addRow(_("用户名"), username_edit)
        layout.addRow(_("密码"), password_edit)
        buttons = QHBoxLayout()
        ok_btn = QPushButton(_("确定"), dialog)
        buttons.addStretch(1)
        buttons.addWidget(ok_btn)
        layout.addRow(buttons)
        ok_btn.clicked.connect(dialog.accept)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return None
        username = username_edit.text().strip()
        password = password_edit.text()
        if not username or not password:
            return None
        return username, password

    def _on_sign(self) -> None:
        template_dir = self._current()
        if template_dir is None:
            return
        credentials = self._ask_password()
        if credentials is None:
            return
        username, password = credentials
        self._sign_btn.setEnabled(False)
        worker = _SignWorker(template_dir, username, password, "template.yaml", parent=self)

        def _done(fingerprint: str) -> None:
            ToastManager.instance().success(_(f"模板已签名（指纹 {fingerprint}）"))
            self.refresh()

        def _failed(message: str) -> None:
            ToastManager.instance().error(_(f"签名失败：{message.splitlines()[0]}"))
            self._sign_btn.setEnabled(True)

        worker.succeeded.connect(_done)
        worker.failed.connect(_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _on_upload(self) -> None:
        template_dir = self._current()
        if template_dir is None:
            return
        credentials = self._ask_password()
        if credentials is None:
            return
        username, password = credentials
        dialog = UploadMarketDialog(self, kind="template", defaults={}, username=username)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        fields = dialog.template_fields()
        self._upload_btn.setEnabled(False)
        try:
            payload = build_template_upload(
                template_dir,
                username=username,
                password=password,
                template_id=fields["id"],
                name=fields["name"],
                version=fields["version"],
                category=fields["category"],
                summary=fields["summary"],
                listing=dialog.listing(),
            )
        except Exception as exc:  # noqa: BLE001
            ToastManager.instance().error(_(f"打包失败：{exc}"))
            self._upload_btn.setEnabled(True)
            return
        worker = _UploadWorker(
            "template",
            payload,
            title=_(f"提交模板 {fields['id']}（{username}）"),
            body=pr_body(_("模板"), fields["id"], username),
            parent=self,
        )

        def _done(url: str) -> None:
            ToastManager.instance().success(_(f"PR 已创建：{url}"))
            self._upload_btn.setEnabled(True)

        def _failed(message: str) -> None:
            ToastManager.instance().error(_(f"上传失败：{message.splitlines()[0]}"))
            self._upload_btn.setEnabled(True)

        worker.succeeded.connect(_done)
        worker.failed.connect(_failed)
        worker.finished.connect(worker.deleteLater)
        worker.start()


class UploadMarketDialog(QDialog):
    """上传市场对话框：listing 编辑（模板另填元数据）。"""

    def __init__(self, parent: QWidget | None, *, kind: str, defaults: dict[str, Any], username: str) -> None:
        super().__init__(parent)
        self._kind = kind
        self.setWindowTitle(_("上传市场（提交审核）"))
        self.setModal(True)
        self.resize(560, 460)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        if kind == "template":
            form = QFormLayout()
            self._tpl_id = QLineEdit()
            self._tpl_name = QLineEdit()
            self._tpl_version = QLineEdit("1.0.0")
            self._tpl_category = QLineEdit("generic")
            self._tpl_summary = QLineEdit()
            form.addRow(_("模板 ID"), self._tpl_id)
            form.addRow(_("名称"), self._tpl_name)
            form.addRow(_("版本"), self._tpl_version)
            form.addRow(_("类别"), self._tpl_category)
            form.addRow(_("简介"), self._tpl_summary)
            layout.addLayout(form)
            self._tpl_id.setPlaceholderText(_("如 my/tpl（小写字母/数字/斜杠）"))

        layout.addWidget(QLabel(_("功能说明（listing.md，展示给所有用户）")))
        self._listing = QTextEdit()
        self._listing.setPlaceholderText(_("描述这个插件/模板做什么、何时用、要什么权限。"))
        layout.addWidget(self._listing, 1)

        note = QLabel(
            _(
                "提交后维护者将收到 PR 并审核；审核通过后由维护者用其密钥补充签名并发布到市场，所有用户即可看到。需要本机安装并登录 GitHub CLI（gh auth login）。"
            )
        )
        note.setObjectName("mutedLabel")
        note.setWordWrap(True)
        layout.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel_btn = QPushButton(_("取消"))
        cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(cancel_btn)
        self._ok_btn = QPushButton(_("提交审核"))
        self._ok_btn.setProperty("primary", True)
        self._ok_btn.clicked.connect(self.accept)
        buttons.addWidget(self._ok_btn)
        layout.addLayout(buttons)

    def listing(self) -> str:
        return self._listing.toPlainText().strip()

    def template_fields(self) -> dict[str, str]:
        return {
            "id": self._tpl_id.text().strip(),
            "name": self._tpl_name.text().strip(),
            "version": self._tpl_version.text().strip() or "1.0.0",
            "category": self._tpl_category.text().strip() or "generic",
            "summary": self._tpl_summary.text().strip(),
        }


def build_market_home_tabs(root: Path, tabs: QTabWidget) -> None:
    """在市场页追加「私人」「本地」两个栏目 tab。"""
    private_pane = QWidget()
    private_layout = QVBoxLayout(private_pane)
    private_layout.setContentsMargins(0, 0, 0, 0)
    private_tabs = QTabWidget()
    private_tabs.addTab(_LocalPluginsPane(root, "private"), _("插件"))
    private_tabs.addTab(_LocalTemplatesPane(root), _("模板"))
    private_layout.addWidget(private_tabs)
    tabs.addTab(private_pane, _("私人"))

    local_pane = QWidget()
    local_layout = QVBoxLayout(local_pane)
    local_layout.setContentsMargins(0, 0, 0, 0)
    local_tabs = QTabWidget()
    local_tabs.addTab(_LocalPluginsPane(root, "local"), _("插件"))
    local_layout.addWidget(local_tabs)
    tabs.addTab(local_pane, _("本地"))
