"""首次启动身份引导：无本地身份时弹出，创建后展示公钥指纹。"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...plugins.identity import IdentityStore
from ..i18n import _
from ..widgets.toast import ToastManager


class IdentityWelcomeDialog(QDialog):
    """首次启动引导：创建本地身份（用户名+密码 → 密钥对 → OS 密钥库加密）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(_("欢迎使用插件生态"))
        self.setModal(True)
        self.resize(480, 360)
        self.setObjectName("identityWelcome")

        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        title = QLabel(_("创建你的插件作者身份"))
        title.setObjectName("homeTitle")
        layout.addWidget(title)

        intro = QLabel(
            _(
                "你可以在 OmniCrawler 中制作自己的插件与模板。本应用将用你的用户名和密码自动生成一对加密密钥：公钥用于署名与分享，私钥加密后存入系统密钥库，绝不落盘明文。"
            )
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self._username = QLineEdit()
        self._username.setPlaceholderText(_("用户名（本地唯一，2-32 位小写字母/数字/下划线/短横线）"))
        self._password = QLineEdit()
        self._password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow(_("用户名"), self._username)
        form.addRow(_("密码"), self._password)
        layout.addLayout(form)

        self._hint = QLabel("")
        self._hint.setWordWrap(True)
        self._hint.setObjectName("mutedLabel")
        layout.addWidget(self._hint)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        later_btn = QPushButton(_("稍后再说"))
        later_btn.clicked.connect(self.reject)
        buttons.addWidget(later_btn)
        create_btn = QPushButton(_("创建身份"))
        create_btn.setProperty("primary", True)
        create_btn.clicked.connect(self._on_create)
        buttons.addWidget(create_btn)
        layout.addLayout(buttons)

    def _on_create(self) -> None:
        username = self._username.text().strip()
        password = self._password.text()
        if not username or not password:
            ToastManager.instance().warning(_("用户名与密码不能为空"))
            return
        try:
            identity = IdentityStore().create(username, password)
        except Exception as exc:  # noqa: BLE001
            ToastManager.instance().error(_(f"创建身份失败：{exc}"))
            return
        self._hint.setText(
            _(
                "身份已创建。你的公钥指纹：{0}\n\n从市场页「私人」栏制作插件：把插件放入项目 plugins/ 目录 → 签名 → 上传市场。"
            ).format(identity.key_fingerprint)
        )
        self._hint.setProperty("class", "success")
        self._username.setEnabled(False)
        self._password.setEnabled(False)
        ToastManager.instance().success(_(f"身份已创建：{username}"))


def maybe_show_identity_welcome(parent: QWidget | None = None) -> bool:
    """无本地身份时弹出引导；返回是否弹过。cryptography 不可用时静默跳过。"""
    try:
        from ...plugins.identity import IdentityStore

        if IdentityStore().list_usernames():
            return False
    except Exception:  # noqa: BLE001 - 身份库不可用时不打扰
        return False
    dialog = IdentityWelcomeDialog(parent)
    dialog.exec()
    return True
