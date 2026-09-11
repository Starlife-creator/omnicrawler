"""PluginMarketView 的「浏览」域 —— 由 plugin_market.py 抽出的 Mixin（P1-3 第四批）。

涵盖目录浏览与详情展示：
- ``_populate_list`` / ``_matches_filters`` / ``_on_selection_changed``：列表填充与筛选
- ``_show_detail`` / ``_on_listing_loaded`` / ``_on_listing_error``：详情面板与清单文本下载
- ``_entry_of`` / ``_update_action_buttons``：条目查找与动作按钮可用性

以 Mixin 形式保留 ``self`` 语义，宿主属性与全部调用点不变。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QListWidgetItem

from ..i18n import _
from .plugin_market_logic import (
    _TYPE_LABELS,
    _compatibility,
    _entry_plugin_types,
    _entry_strings,
    _install_block_reason,
    _permission_risk,
)
from .plugin_market_workers import _ListingWorker

if TYPE_CHECKING:
    from PySide6.QtWidgets import (
        QComboBox,
        QLabel,
        QLineEdit,
        QListWidget,
        QPushButton,
        QTextEdit,
        QWidget,
    )

    # 类型检查期把宿主视作 QWidget；运行期仍为 object，不改变 PluginMarketView 的 MRO。
    _Base = QWidget
else:
    _Base = object


class MarketBrowseMixin(_Base):
    """浏览域：目录列表、筛选、详情与动作按钮。"""

    # ---- 宿主契约：实例属性 ----
    _state: str
    _catalog: dict[str, Any] | None
    _catalog_url: str
    _egress: Any
    _enabled_plugin_ids: set[str]
    _selected_id: str | None
    _listing_worker: _ListingWorker | None
    _search_edit: QLineEdit
    _type_filter: QComboBox
    _mode_filter: QComboBox
    _risk_filter: QComboBox
    _list: QListWidget
    _footer: QLabel
    _detail_name: QLabel
    _detail_meta: QLabel
    _detail_tags: QLabel
    _detail_capabilities: QLabel
    _detail_summary: QLabel
    _detail_listing: QTextEdit
    _install_btn: QPushButton
    _uninstall_btn: QPushButton
    _enable_btn: QPushButton
    _disable_btn: QPushButton
    _verify_btn: QPushButton

    # ---- 宿主契约：方法（由 MarketActionsMixin 提供）----
    if TYPE_CHECKING:
        def _is_installed(self, plugin_id: str) -> bool: ...
        def _installed_ids(self) -> list[str]: ...
    def _populate_list(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        plugins = (self._catalog or {}).get("plugins", []) if self._catalog else []
        visible = [entry for entry in plugins if self._matches_filters(entry)]
        for entry in visible:
            pid = entry.get("id", "")
            name = entry.get("name", pid)
            version = entry.get("version", "")
            installed = self._is_installed(pid)
            plugin_types = _entry_plugin_types(entry)
            mode = str(entry.get("execution_mode") or "subprocess")
            _risk_key, risk_label = _permission_risk(entry)
            type_label = "/".join(_TYPE_LABELS.get(item, item) for item in plugin_types) or _("类型未知")
            mode_label = _("隔离") if mode == "subprocess" else _("进程内")
            label = f"{name}  v{version}" if version else name
            label += f"  ·  {type_label} · {mode_label} · {risk_label}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, pid)
            enabled = pid in self._enabled_plugin_ids
            if enabled:
                item.setText(_(f"● [已启用] ✓ {label}"))
            elif installed:
                item.setText(_(f"○ [已禁用] ✓ {label}"))
            else:
                item.setText(_(f"— [未安装] {label}"))
            compatibility = _compatibility(entry)[1]
            project_state = (
                _("当前项目已启用")
                if enabled
                else (_("已安装，但在当前项目禁用") if installed else _("尚未安装"))
            )
            item.setToolTip(
                f"{pid}\n{project_state}\n"
                f"{type_label} · {mode_label} · {risk_label} · {compatibility}"
            )
            self._list.addItem(item)

        # 离线时补充展示本地已安装但不在目录中的插件
        if self._state == "offline":
            for pid in self._installed_ids():
                if not any(e.get("id") == pid for e in plugins):
                    item = QListWidgetItem(_(f"✓ {pid}  （本地已安装）"))
                    item.setData(Qt.ItemDataRole.UserRole, pid)
                    item.setToolTip(pid)
                    self._list.addItem(item)

        self._list.blockSignals(False)
        if self._state == "ready":
            self._footer.setText(_(f"显示 {len(visible)} / {len(plugins)} 个已审核插件。"))
        if self._list.count() > 0:
            self._list.setCurrentRow(0)
        else:
            self._show_detail(None)

    def _matches_filters(self, entry: dict[str, Any]) -> bool:
        query = self._search_edit.text().strip().casefold()
        plugin_types = _entry_plugin_types(entry)
        if query:
            searchable = " ".join(
                [
                    str(entry.get("id", "")),
                    str(entry.get("name", "")),
                    str(entry.get("category", "")),
                    str(entry.get("summary", "")),
                    *_entry_strings(entry, "tags"),
                    *plugin_types,
                ]
            ).casefold()
            if query not in searchable:
                return False
        selected_type = str(self._type_filter.currentData() or "")
        if selected_type and selected_type not in plugin_types:
            return False
        selected_mode = str(self._mode_filter.currentData() or "")
        mode = str(entry.get("execution_mode") or "subprocess")
        if selected_mode and selected_mode != mode:
            return False
        selected_risk = str(self._risk_filter.currentData() or "")
        if selected_risk and selected_risk != _permission_risk(entry)[0]:
            return False
        return True

    def _on_selection_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            return
        pid = current.data(Qt.ItemDataRole.UserRole)
        self._show_detail(pid)

    def _show_detail(self, plugin_id: str | None) -> None:
        self._selected_id = plugin_id
        if not plugin_id:
            self._detail_name.setText(_("未选择插件"))
            self._detail_meta.setText("")
            self._detail_tags.setText("")
            self._detail_capabilities.setText("")
            self._detail_summary.setText("")
            self._detail_listing.setText("")
            self._update_action_buttons()
            return

        entry = self._entry_of(plugin_id)
        installed = self._is_installed(plugin_id)
        name = (entry or {}).get("name", plugin_id)
        version = (entry or {}).get("version", "")
        publisher = (entry or {}).get("publisher", "")
        category = (entry or {}).get("category", "")
        compat = (entry or {}).get("compatible_core", "")
        license_ = (entry or {}).get("license", "")
        tags = _entry_strings(entry or {}, "tags")
        summary = (entry or {}).get("summary", "")
        plugin_types = _entry_plugin_types(entry or {})
        mode = str((entry or {}).get("execution_mode") or "subprocess")
        permissions = list(_entry_strings(entry or {}, "permissions"))
        domains = list(_entry_strings(entry or {}, "domains"))
        risk_label = _permission_risk(entry or {})[1]
        compatibility = _compatibility(entry or {})[1]

        self._detail_name.setText(name)
        meta_parts = [
            f"v{version}" if version else "",
            publisher,
            category,
            _(f"兼容 {compat}") if compat else "",
            license_,
            _("当前项目已启用") if plugin_id in self._enabled_plugin_ids else _("当前项目未启用"),
        ]
        self._detail_meta.setText(" · ".join(p for p in meta_parts if p))
        self._detail_tags.setText(_("标签: ") + (", ".join(tags) if tags else "—"))
        type_text = ", ".join(_TYPE_LABELS.get(item, item) for item in plugin_types) or _("未知")
        mode_text = _("隔离子进程") if mode == "subprocess" else _("进程内（高风险审批）")
        permission_text = ", ".join(permissions) if permissions else _("无额外权限")
        domain_text = _("；域名：") + ", ".join(domains) if domains else ""
        ui_notice = (
            _("\n⚠ 原生 UI 只能作为受信任本地进程内插件运行。")
            if "ui" in plugin_types
            else ""
        )
        self._detail_capabilities.setText(
            _("运行扩展点：{0}\n执行模式：{1}\n权限：{2}（{3}）{4}\n兼容性：{5}").format(
                type_text,
                mode_text,
                permission_text,
                risk_label,
                domain_text,
                compatibility,
            )
            + ui_notice
        )
        self._detail_summary.setText(summary)
        self._detail_listing.setText(
            _(
                "（加载功能说明中...）"
                if entry and entry.get("description_file")
                else "（该插件未提供功能说明）"
            )
        )
        self._update_action_buttons(installed=installed)

        # 懒加载 listing.md
        if entry and entry.get("description_file"):
            rel = entry["description_file"]
            source = (self._catalog or {}).get("_source", self._catalog_url)
            if source:
                self._listing_worker = _ListingWorker(source, rel, self._egress, parent=self)
                self._listing_worker.succeeded.connect(
                    lambda text, pid=plugin_id: self._on_listing_loaded(pid, text)
                )
                self._listing_worker.failed.connect(lambda _e, pid=plugin_id: self._on_listing_error(pid))
                self._listing_worker.finished.connect(self._listing_worker.deleteLater)
                self._listing_worker.start()

    def _on_listing_loaded(self, plugin_id: str, text: str) -> None:
        if plugin_id == self._selected_id:
            self._detail_listing.setText(text)

    def _on_listing_error(self, plugin_id: str) -> None:
        if plugin_id == self._selected_id:
            self._detail_listing.setText(_("（功能说明加载失败）"))

    def _entry_of(self, plugin_id: str) -> dict[str, Any] | None:
        if not self._catalog:
            return None
        for entry in self._catalog.get("plugins", []):
            if entry.get("id") == plugin_id:
                return cast("dict[str, Any]", entry)
        return None

    def _update_action_buttons(self, installed: bool | None = None) -> None:
        pid = self._selected_id
        if pid is None:
            self._install_btn.setEnabled(False)
            self._uninstall_btn.setEnabled(False)
            self._enable_btn.setEnabled(False)
            self._disable_btn.setEnabled(False)
            self._verify_btn.setEnabled(False)
            return
        if installed is None:
            installed = self._is_installed(pid)
        can_network = self._state == "ready"
        entry = self._entry_of(pid)
        block_reason = _install_block_reason(entry) if entry else ""
        self._install_btn.setEnabled(can_network and not installed and not block_reason)
        self._install_btn.setText(_("重装") if installed else _("安装"))
        self._install_btn.setToolTip(block_reason)
        self._uninstall_btn.setEnabled(installed)
        self._enable_btn.setEnabled(installed and not block_reason)
        self._enable_btn.setText(
            _("重新授权") if pid in self._enabled_plugin_ids else _("启用到当前项目")
        )
        self._disable_btn.setEnabled(installed and pid in self._enabled_plugin_ids)
        self._verify_btn.setEnabled(installed)
