"""PluginMarketView 的「安装流程」域 —— 由 plugin_market.py 抽出的 Mixin（P1-3 第五批）。

涵盖从点击安装到落盘完成的完整链路：
- ``_on_install``：确认审查文案（权限风险/兼容性/来源）→ 启动 _InstallWorker
- ``_on_installed`` / ``_on_install_error``：安装终态收尾（创作者签名时触发 P2P 信任询问）
- ``_prompt_p2p_trust`` / ``_open_identity_dialog``：创作者信任与身份管理对话框

以 Mixin 形式保留 ``self`` 语义，宿主属性与全部调用点不变。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..i18n import _
from ..widgets.toast import ToastManager
from .plugin_market_logic import (
    _install_block_reason,
    _install_review_text,
    _permission_risk,
)
from .plugin_market_workers import _InstallWorker

if TYPE_CHECKING:
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QLabel, QPushButton, QWidget

    # 类型检查期把宿主视作 QWidget；运行期仍为 object，不改变 PluginMarketView 的 MRO。
    _Base = QWidget
else:
    _Base = object


class MarketInstallMixin(_Base):
    """安装流程域：审查确认、安装 worker 编排与创作者信任询问。"""

    # ---- 宿主契约：实例属性 ----
    _state: str
    _catalog: dict[str, Any] | None
    _catalog_url: str
    _dest_root: Path
    _egress: Any
    _trust_source: str
    _selected_id: str | None
    _install_worker: _InstallWorker | None
    _install_btn: QPushButton
    _footer: QLabel
    installation_completed: Signal
    activation_requested: Signal

    # ---- 宿主契约：方法（由 MarketBrowseMixin 提供）----
    if TYPE_CHECKING:
        def _entry_of(self, plugin_id: str) -> dict[str, Any] | None: ...
        def _populate_list(self) -> None: ...
        def _update_action_buttons(self, installed: bool | None = None) -> None: ...
    def _on_install(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        pid = self._selected_id
        if not pid or self._state != "ready":
            ToastManager.instance().warning(_("请先联网刷新并选择插件"))
            return
        entry = self._entry_of(pid)
        if entry is None:
            ToastManager.instance().error(_("目录中找不到所选插件"))
            return
        block_reason = _install_block_reason(entry)
        if block_reason:
            ToastManager.instance().warning(block_reason)
            return
        if _permission_risk(entry)[0] != "low":
            reply = QMessageBox.question(
                self,
                _("安装前权限审查"),
                _install_review_text(entry) + _("\n\n确认继续安装？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return
        self._install_btn.setEnabled(False)
        self._footer.setText(_(f"正在下载并校验 {pid} ..."))
        source = str((self._catalog or {}).get("_source") or self._catalog_url)
        self._install_worker = _InstallWorker(
            pid, source, self._dest_root, self._trust_source, self._egress, parent=self
        )
        self._install_worker.succeeded.connect(self._on_installed)
        self._install_worker.failed.connect(self._on_install_error)
        self._install_worker.finished.connect(self._install_worker.deleteLater)
        self._install_worker.start()

    def _on_installed(self, plugin_id: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        ToastManager.instance().success(_(f"已安装并校验通过：{plugin_id}"))
        self._footer.setText(
            _(f"已安装 {plugin_id} 到 {self._dest_root / plugin_id}；请求的权限仍需在项目插件管理中批准")
        )
        self._populate_list()
        self._update_action_buttons(installed=True)
        self.installation_completed.emit(plugin_id)
        self._prompt_p2p_trust(plugin_id)
        reply = QMessageBox.question(
            self,
            _("启用插件"),
            _("插件已安全安装，但尚未在当前项目启用。是否现在绑定版本、载荷和权限并启用？"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            self.activation_requested.emit(plugin_id)

    def _open_identity_dialog(self) -> None:
        from .identity_dialog import IdentityDialog

        dialog = IdentityDialog(parent=self)
        dialog.exec()

    def _prompt_p2p_trust(self, plugin_id: str) -> None:
        """安装的插件仅带创作者签名（无维护者签名）时，询问是否信任该创作者。"""
        from PySide6.QtWidgets import QMessageBox

        from ...plugins.trust import TrustedUserList, TrustLevel, verify_plugin_trust

        plugin_dir = self._dest_root / plugin_id
        decision = verify_plugin_trust(plugin_dir, self._trust_source, TrustedUserList())
        if decision.level != TrustLevel.CreatorUntrusted or decision.creator is None:
            return
        creator = decision.creator
        reply = QMessageBox.question(
            self,
            _("检测到外部插件"),
            _(
                "检测到创作者签名的插件：{0}\n\n"
                "插件作者：{1}\n公钥指纹：{2}\n\n"
                "该插件未经市场审核（无维护者签名）。是否信任此用户？\n"
                "信任后，该用户的所有插件将自动信任。"
            ).format(plugin_id, creator.username, creator.key_fingerprint),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if TrustedUserList().add(creator, source="p2p", path_hint=f"（{plugin_id}）"):
            ToastManager.instance().success(
                _(f"已信任创作者 {creator.username}（指纹 {creator.key_fingerprint}）")
            )
        else:
            ToastManager.instance().info(_(f"创作者 {creator.username} 已在信任列表"))

    def _on_install_error(self, msg: str) -> None:
        ToastManager.instance().error(_(f"安装失败：{msg.split(chr(10))[0]}"))
        self._footer.setText(_(f"安装失败：{msg.split(chr(10))[0]}"))
        self._update_action_buttons()
