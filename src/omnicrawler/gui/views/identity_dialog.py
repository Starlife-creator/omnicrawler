"""身份与信任管理对话框（阶段 2，对齐 Helios 身份系统）。

- 本地身份：创建（用户名 + 密码，私钥密码加密入 OS 密钥库）/ 删除。
- 信任列表：添加（公钥 PEM + 显示名，绑定公钥本体）/ 撤销。纯本地决策，
  不同步服务器。
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...plugins.identity import IdentityStore
from ...plugins.trust import TrustedUserList
from ..design_system import FONT_SIZE, RADIUS, ThemeManager
from ..i18n import _
from ..widgets.toast import ToastManager


class IdentityDialog(QDialog):
    """插件生态身份与信任管理。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("身份与信任"))
        self.setModal(True)
        self.resize(520, 480)
        self.setObjectName("identityDialog")
        self.setAccessibleName(_("身份与信任管理"))

        self._build_ui()
        self._apply_style()
        self.refresh()

    # ── UI ───────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(14)

        # 身份区
        id_label = QLabel(_("本地身份（私钥密码加密存入 OS 密钥库）"))
        id_label.setObjectName("sectionSubtitle")
        root.addWidget(id_label)

        self._id_list = QListWidget()
        self._id_list.setMinimumHeight(90)
        root.addWidget(self._id_list)

        id_form = QFormLayout()
        self._id_name = QLineEdit()
        self._id_name.setPlaceholderText(_("用户名（本地唯一）"))
        self._id_password = QLineEdit()
        self._id_password.setEchoMode(QLineEdit.EchoMode.Password)
        self._id_password.setPlaceholderText(_("密码（不落盘明文）"))
        id_form.addRow(_("用户名"), self._id_name)
        id_form.addRow(_("密码"), self._id_password)
        root.addLayout(id_form)

        id_buttons = QHBoxLayout()
        self._create_btn = QPushButton(_("创建身份"))
        self._create_btn.setProperty("primary", True)
        self._create_btn.clicked.connect(self._on_create_identity)
        id_buttons.addWidget(self._create_btn)
        self._delete_btn = QPushButton(_("删除选中身份"))
        self._delete_btn.clicked.connect(self._on_delete_identity)
        id_buttons.addWidget(self._delete_btn)
        id_buttons.addStretch(1)
        root.addLayout(id_buttons)

        # 信任区
        trust_label = QLabel(_("信任列表（创作者签名插件：指纹在列表内即自动加载）"))
        trust_label.setObjectName("sectionSubtitle")
        root.addWidget(trust_label)

        self._trust_list = QListWidget()
        self._trust_list.setMinimumHeight(90)
        root.addWidget(self._trust_list)

        trust_form = QFormLayout()
        self._trust_name = QLineEdit()
        self._trust_name.setPlaceholderText(_("创作者显示名"))
        self._trust_pubkey = QLineEdit()
        self._trust_pubkey.setPlaceholderText(_("创作者公钥 PEM 文件路径或文本"))
        trust_form.addRow(_("显示名"), self._trust_name)
        trust_form.addRow(_("公钥"), self._trust_pubkey)
        root.addLayout(trust_form)

        trust_buttons = QHBoxLayout()
        self._trust_add_btn = QPushButton(_("信任创作者"))
        self._trust_add_btn.setProperty("primary", True)
        self._trust_add_btn.clicked.connect(self._on_trust_add)
        trust_buttons.addWidget(self._trust_add_btn)
        self._trust_revoke_btn = QPushButton(_("撤销选中信任"))
        self._trust_revoke_btn.clicked.connect(self._on_trust_revoke)
        trust_buttons.addWidget(self._trust_revoke_btn)
        trust_buttons.addStretch(1)
        root.addLayout(trust_buttons)

        close_buttons = QHBoxLayout()
        close_buttons.addStretch(1)
        close = QPushButton(_("关闭"))
        close.clicked.connect(self.accept)
        close_buttons.addWidget(close)
        root.addLayout(close_buttons)

    def _apply_style(self) -> None:
        t = ThemeManager.instance().tokens
        self.setStyleSheet(f"""
            QLabel#sectionSubtitle {{
                font-size: {FONT_SIZE["body"]}px;
                color: {t.text};
                font-weight: 600;
            }}
            QListWidget {{
                border: 1px solid {t.border};
                border-radius: {RADIUS["sm"]}px;
                padding: 4px;
                background: {t.surface};
            }}
            QLineEdit {{
                border: 1px solid {t.border};
                border-radius: {RADIUS["sm"]}px;
                padding: 6px 8px;
                background: {t.surface};
                color: {t.text};
            }}
        """)

    # ── 身份操作 ─────────────────────────────────────────
    def refresh(self) -> None:
        self._id_list.clear()
        if IdentityStore is not None:
            for username in IdentityStore().list_usernames():
                self._id_list.addItem(username)
        self._trust_list.clear()
        for user in TrustedUserList().list_users():
            self._trust_list.addItem(f"{user.username}  {user.key_fingerprint}  ({user.source})")

    def _on_create_identity(self) -> None:
        if IdentityStore is None:
            ToastManager.instance().warning(_("插件身份系统不可用（缺少 cryptography）"))
            return
        username = self._id_name.text().strip()
        password = self._id_password.text()
        if not username or not password:
            ToastManager.instance().warning(_("用户名与密码不能为空"))
            return
        try:
            identity = IdentityStore().create(username, password)
        except Exception as exc:  # noqa: BLE001
            ToastManager.instance().error(_(f"创建身份失败：{exc}"))
            return
        ToastManager.instance().success(_(f"身份已创建：{username}\n公钥指纹：{identity.key_fingerprint}"))
        self._id_name.clear()
        self._id_password.clear()
        self.refresh()

    def _on_delete_identity(self) -> None:
        if IdentityStore is None:
            return
        item = self._id_list.currentItem()
        if item is None:
            ToastManager.instance().warning(_("请先选择要删除的身份"))
            return
        username = item.text()
        password = self._id_password.text()
        if not password:
            ToastManager.instance().warning(_("删除身份需要输入密码"))
            return
        if (
            QMessageBox.question(
                self,
                _("删除身份"),
                _(f"确定删除身份 {username}？此操作不可恢复。"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            IdentityStore().delete(username, password)
        except Exception as exc:  # noqa: BLE001
            ToastManager.instance().error(_(f"删除失败：{exc}"))
            return
        ToastManager.instance().success(_(f"身份已删除：{username}"))
        self._id_password.clear()
        self.refresh()

    # ── 信任列表操作 ─────────────────────────────────────
    def _on_trust_add(self) -> None:
        name = self._trust_name.text().strip()
        pubkey_value = self._trust_pubkey.text().strip()
        if not name or not pubkey_value:
            ToastManager.instance().warning(_("显示名与公钥不能为空"))
            return
        try:
            from omnicrawler.plugins.identity import (
                CreatorIdentity,
                public_key_bytes_from_pem,
            )

            creator = CreatorIdentity(
                username=name, public_key=public_key_bytes_from_pem(pubkey_value)
            )
        except Exception as exc:  # noqa: BLE001 - 用户输入解析失败，提示即可
            ToastManager.instance().error(_(f"公钥无效：{exc}"))
            return
        added = TrustedUserList().add(creator, source="manual")
        ToastManager.instance().success(
            _(f"已信任 {name}（指纹 {creator.key_fingerprint}）" if added else f"指纹 {creator.key_fingerprint} 已在信任列表")
        )
        self._trust_name.clear()
        self._trust_pubkey.clear()
        self.refresh()

    def _on_trust_revoke(self) -> None:
        item = self._trust_list.currentItem()
        if item is None:
            ToastManager.instance().warning(_("请先选择要撤销的信任"))
            return
        fingerprint = item.text().split()[-2]
        if TrustedUserList().revoke(fingerprint):
            ToastManager.instance().success(_(f"已撤销信任：{fingerprint}"))
        self.refresh()
