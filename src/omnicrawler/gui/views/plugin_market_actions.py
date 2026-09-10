"""PluginMarketView 的「插件动作」域 —— 由 plugin_market.py 抽出的 Mixin（P1-3 第三批）。

涵盖已安装插件的状态查询与动作：
- ``_is_installed`` / ``_installed_ids``：安装状态查询（唯一宿主依赖 ``_dest_root``）
- ``_on_enable`` / ``_on_disable`` / ``set_enabled_plugins``：启用/禁用（发信号由宿主联动配置）
- ``_on_uninstall`` / ``_on_verify``：卸载（确认后删除目录）与重新验签

以 Mixin 形式保留 ``self`` 语义，宿主属性与全部调用点不变。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...plugins.market_client import verify_installed
from ..i18n import _
from ..widgets.toast import ToastManager
from .plugin_market_logic import _install_block_reason

if TYPE_CHECKING:
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import QLabel, QWidget

    # 类型检查期把宿主视作 QWidget；运行期仍为 object，不改变 PluginMarketView 的 MRO。
    _Base = QWidget
else:
    _Base = object

LOGGER = logging.getLogger(__name__)


class MarketActionsMixin(_Base):
    """插件动作域：安装状态查询 + 启用/禁用 + 卸载/验签。"""

    # ---- 宿主契约：实例属性（由 PluginMarketView.__init__ / 类体提供）----
    _dest_root: Path
    _trust_source: str
    _selected_id: str | None
    _enabled_plugin_ids: set[str]
    _footer: QLabel
    activation_requested: Signal
    deactivation_requested: Signal
    uninstall_completed: Signal

    # ---- 宿主契约：方法（留在宿主的共享能力）----
    if TYPE_CHECKING:
        def _entry_of(self, plugin_id: str) -> dict[str, Any] | None: ...
        def _populate_list(self) -> None: ...
        def _update_action_buttons(self, installed: bool | None = None) -> None: ...
    def _is_installed(self, plugin_id: str) -> bool:
        target = self._dest_root / plugin_id
        try:
            return (target / "plugin.py").is_file() and (target / "plugin.py.sig").is_file()
        except OSError as exc:
            LOGGER.warning(_("无法读取已安装插件目录 %s: %s"), target, exc)
            return False

    def _installed_ids(self) -> list[str]:
        if not self._dest_root.is_dir():
            return []
        installed: list[str] = []
        try:
            candidates = list(self._dest_root.iterdir())
        except OSError as exc:
            LOGGER.warning(_("无法读取插件安装根目录 %s: %s"), self._dest_root, exc)
            return installed
        for candidate in candidates:
            try:
                if candidate.is_dir() and (candidate / "plugin.py.sig").is_file():
                    installed.append(candidate.name)
            except OSError as exc:
                LOGGER.warning(_("忽略不可读的插件安装项 %s: %s"), candidate, exc)
        return installed

    def _on_enable(self) -> None:
        pid = self._selected_id
        if not pid or not self._is_installed(pid):
            ToastManager.instance().warning(_("请先安装插件"))
            return
        entry = self._entry_of(pid)
        if entry:
            block_reason = _install_block_reason(entry)
            if block_reason:
                ToastManager.instance().warning(block_reason)
                return
        self.activation_requested.emit(pid)

    def _on_disable(self) -> None:
        pid = self._selected_id
        if not pid or not self._is_installed(pid):
            ToastManager.instance().warning(_("所选插件尚未安装"))
            return
        if pid not in self._enabled_plugin_ids:
            ToastManager.instance().info(_("所选插件已经在当前项目禁用"))
            return
        self.deactivation_requested.emit(pid)

    def _on_uninstall(self) -> None:
        from PySide6.QtWidgets import QMessageBox

        pid = self._selected_id
        if not pid or not self._is_installed(pid):
            ToastManager.instance().warning(_("未选择已安装插件"))
            return
        reply = QMessageBox.question(
            self,
            _("卸载插件"),
            _(f"确定卸载插件 {pid}？\n将从 {self._dest_root / pid} 移除。"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        import shutil

        target = self._dest_root / pid
        try:
            shutil.rmtree(target, ignore_errors=True)
            self._enabled_plugin_ids.discard(pid)
            self.uninstall_completed.emit(pid)
            ToastManager.instance().success(_(f"已卸载：{pid}"))
            self._footer.setText(_(f"已卸载 {pid}"))
        except OSError as exc:
            ToastManager.instance().error(_(f"卸载失败：{exc}"))
        self._populate_list()
        self._update_action_buttons(installed=False)

    def _on_verify(self) -> None:
        pid = self._selected_id
        if not pid or not self._is_installed(pid):
            ToastManager.instance().warning(_("未选择已安装插件"))
            return
        ok, reason = verify_installed(self._dest_root, pid, self._trust_source)
        if ok:
            ToastManager.instance().success(_(f"{pid} 签名校验通过"))
        else:
            ToastManager.instance().error(_(f"{pid} 校验失败：{reason}"))
        self._footer.setText(_(f"校验 {pid}：{reason}"))

    def set_enabled_plugins(self, plugin_ids: set[str] | list[str] | tuple[str, ...]) -> None:
        self._enabled_plugin_ids = {str(item) for item in plugin_ids}
        self._populate_list()
