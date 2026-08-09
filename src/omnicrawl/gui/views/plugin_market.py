"""插件市场面板 — 策展式插件分发 GUI。

联网后从 ``catalog_url`` 拉取审核通过的插件目录，展示名称/版本/说明，
用户按需下载安装；每份插件均经 ed25519 离线验签（fail-closed）后才落盘到
``plugins_installed/``。离线时仅展示已安装列表并禁用联网操作。

设计约束（见 docs/ADR-001-plugin-catalog.md）：
- 仅联网时从远程拉取；远程失败可回退到本地 ``OmniCrawler-market/``（开发态便利）。
- 所有下载均用打包内的信任根公钥验签，绝不信任网络本身。
- 安装目录 ``plugins_installed/<id>/`` 已被默认 ``plugins.paths`` 覆盖，落盘即自动加载。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.config import DEFAULTS
from ...plugins.market_client import (
    download_and_verify,
    fetch_catalog,
    fetch_resource,
    verify_installed,
)
from ..core.background_worker import BackgroundWorker
from ..design_system import FONT_FAMILY_MONO, FONT_SIZE, RADIUS, ThemeManager
from ..i18n import _
from ..widgets.status_indicator import StatusIndicator
from ..widgets.toast import ToastManager


def _project_root_of(base: str | Path | None) -> Path:
    if base:
        return Path(base)
    # src/omnicrawl/gui/views/plugin_market.py -> 上溯 4 级到项目根
    return Path(__file__).resolve().parents[4]


_CATALOG_PURPOSE = "plugin"


def _market_egress(project_root: Path) -> Any:
    """Lazily build a shared EgressBroker for curated plugin-market traffic.

    The marketplace downloads third-party signed plugins; those requests must
    cross the same policy/budget/audit boundary as every other network egress,
    not ride a raw urllib call.  A fresh default broker is safe here because
    egress defaults only restrict private-network targets and count requests.
    """
    from ...core.config import DEFAULTS, AppConfig, deep_merge
    from ...security.egress import EgressBroker

    raw = deep_merge(dict(DEFAULTS), {"egress": {"audit": True}})
    raw.setdefault("project", {"name": "plugin-market", "workspace": str(project_root)})
    config = AppConfig(Path("<plugin-market>"), project_root, raw, project_root)
    return EgressBroker(config)


# ── 后台任务 ─────────────────────────────────────────────────────
class _CatalogWorker(BackgroundWorker):
    """后台拉取 catalog：先远程，失败回退本地 OmniCrawler-market/。"""

    def __init__(self, catalog_url: str, local_fallback: Path, egress: Any, parent=None) -> None:
        super().__init__(parent)
        self._catalog_url = catalog_url
        self._local_fallback = local_fallback
        self._egress = egress

    def work(self) -> dict[str, Any]:
        try:
            catalog = fetch_catalog(self._catalog_url, egress=self._egress)
            catalog["_source"] = self._catalog_url
            return catalog
        except Exception:
            if self._local_fallback.is_dir():
                local = str(self._local_fallback)
                catalog = fetch_catalog(local)
                catalog["_source"] = local
                return catalog
            raise


class _ListingWorker(BackgroundWorker):
    """后台拉取单个插件的 listing.md 说明。"""

    def __init__(self, catalog_url: str, rel: str, egress: Any, parent=None) -> None:
        super().__init__(parent)
        self._catalog_url = catalog_url
        self._rel = rel
        self._egress = egress

    def work(self) -> str:
        return fetch_resource(self._catalog_url, self._rel, egress=self._egress).decode("utf-8", "replace")


class _InstallWorker(BackgroundWorker):
    """后台下载 + ed25519 验签 + 落盘安装。"""

    def __init__(
        self,
        plugin_id: str,
        catalog_url: str,
        dest_root: Path,
        trust_source: str,
        egress: Any,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._plugin_id = plugin_id
        self._catalog_url = catalog_url
        self._dest_root = dest_root
        self._trust_source = trust_source
        self._egress = egress

    def work(self) -> str:
        download_and_verify(
            self._plugin_id,
            self._catalog_url,
            self._dest_root,
            self._trust_source,
            egress=self._egress,
        )
        return self._plugin_id


# ── 视图 ──────────────────────────────────────────────────────────
class PluginMarketView(QWidget):
    """策展式插件市场面板。

    状态: offline | loading | ready | error
    """

    def __init__(self, project_root: str | Path | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("pluginMarket")
        self.setAccessibleName(_("插件市场"))

        base = _project_root_of(project_root)
        self._base = base
        self._dest_root = base / "plugins_installed"
        self._local_fallback = base.parent / "OmniCrawler-market"
        self._egress = _market_egress(base)

        plugins_cfg = DEFAULTS.get("plugins", {}) if isinstance(DEFAULTS.get("plugins"), dict) else {}
        self._catalog_url: str = str(plugins_cfg.get("catalog_url", ""))
        self._bundled_catalog_dir: str = str(plugins_cfg.get("bundled_catalog_dir", ""))
        trust_cfg = plugins_cfg.get("trust_public_key", "")
        if trust_cfg:
            self._trust_source = str(trust_cfg)
        else:
            self._trust_source = str(base / "configs" / "plugin_trust.pub.pem")

        self._state = "offline"
        self._catalog: dict[str, Any] | None = None
        self._selected_id: str | None = None
        self._auto_loaded = False
        self._catalog_worker: _CatalogWorker | None = None
        self._listing_worker: _ListingWorker | None = None
        self._install_worker: _InstallWorker | None = None

        self._setup_ui()
        self._apply_style()
        ThemeManager.instance().theme_changed.connect(self._apply_style)

    # ── UI 搭建 ────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        title = QLabel(_("市场"))
        title.setObjectName("homeTitle")
        root.addWidget(title)

        from .template_market import TemplateMarketView

        self._tabs = QTabWidget()
        self._tabs.setObjectName("marketTabs")
        plugin_pane = QWidget()
        self._build_plugin_pane(plugin_pane)
        self._tabs.addTab(plugin_pane, _("插件"))
        self._template_market = TemplateMarketView(
            self._catalog_url, self._base, self._trust_source, parent=self
        )
        self._tabs.addTab(self._template_market, _("模板"))
        root.addWidget(self._tabs, 1)

    def _build_plugin_pane(self, pane: QWidget) -> None:
        root = QVBoxLayout(pane)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(14)

        subtitle = QLabel(
            _("审核通过的插件，联网后按需下载安装。每份插件均经 ed25519 签名校验，安装后自动加载。")
        )
        subtitle.setObjectName("sectionSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(subtitle)

        # ── 顶部状态栏 ──
        top_bar = QHBoxLayout()
        self._status_indicator = StatusIndicator(size=14)
        top_bar.addWidget(self._status_indicator)
        self._status_label = QLabel(_("未连接"))
        self._status_label.setObjectName("mutedLabel")
        top_bar.addWidget(self._status_label)

        top_bar.addStretch(1)

        self._source_label = QLabel("")
        self._source_label.setObjectName("mutedLabel")
        self._source_label.setWordWrap(False)
        top_bar.addWidget(self._source_label)

        self._identity_btn = QPushButton(_("身份与信任"))
        self._identity_btn.clicked.connect(self._open_identity_dialog)
        top_bar.addWidget(self._identity_btn)

        self._refresh_btn = QPushButton(_("刷新"))
        self._refresh_btn.clicked.connect(self.refresh)
        top_bar.addWidget(self._refresh_btn)
        root.addLayout(top_bar)

        # ── 主体：左列表 + 右详情 ──
        splitter = QSplitter()
        splitter.setOrientation(Qt.Orientation.Horizontal)

        # 左：插件列表
        list_panel = QFrame()
        list_panel.setProperty("card", True)
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(12, 12, 12, 12)

        list_header = QLabel(_("可用插件"))
        list_header.setObjectName("sectionSubtitle")
        list_layout.addWidget(list_header)

        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.currentItemChanged.connect(self._on_selection_changed)
        list_layout.addWidget(self._list, 1)
        splitter.addWidget(list_panel)

        # 右：详情
        detail_panel = QFrame()
        detail_panel.setProperty("card", True)
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(14, 14, 14, 14)

        self._detail_name = QLabel(_("未选择插件"))
        self._detail_name.setObjectName("detailTitle")
        detail_layout.addWidget(self._detail_name)

        self._detail_meta = QLabel("")
        self._detail_meta.setObjectName("mutedLabel")
        self._detail_meta.setWordWrap(True)
        detail_layout.addWidget(self._detail_meta)

        self._detail_tags = QLabel("")
        self._detail_tags.setObjectName("tagLabel")
        self._detail_tags.setWordWrap(True)
        detail_layout.addWidget(self._detail_tags)

        self._detail_summary = QLabel("")
        self._detail_summary.setWordWrap(True)
        detail_layout.addWidget(self._detail_summary)

        listing_header = QLabel(_("功能说明"))
        listing_header.setObjectName("sectionSubtitle")
        detail_layout.addWidget(listing_header)

        self._detail_listing = QTextEdit()
        self._detail_listing.setReadOnly(True)
        self._detail_listing.setMinimumHeight(160)
        detail_layout.addWidget(self._detail_listing, 1)

        # 操作按钮
        btn_row = QHBoxLayout()
        self._install_btn = QPushButton(_("安装"))
        self._install_btn.setProperty("primary", True)
        self._install_btn.clicked.connect(self._on_install)
        btn_row.addWidget(self._install_btn)

        self._uninstall_btn = QPushButton(_("卸载"))
        self._uninstall_btn.clicked.connect(self._on_uninstall)
        btn_row.addWidget(self._uninstall_btn)

        self._verify_btn = QPushButton(_("校验"))
        self._verify_btn.clicked.connect(self._on_verify)
        btn_row.addWidget(self._verify_btn)
        btn_row.addStretch(1)
        detail_layout.addLayout(btn_row)

        splitter.addWidget(detail_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        root.addWidget(splitter, 1)

        # ── 底部状态 ──
        self._footer = QLabel("")
        self._footer.setObjectName("mutedLabel")
        self._footer.setWordWrap(True)
        root.addWidget(self._footer)

        self._set_offline_state(_("尚未加载。点击「刷新」从插件目录拉取（需联网）。"))

    # ── 样式 ───────────────────────────────────────────────────
    def _apply_style(self, *_args: Any) -> None:
        t = ThemeManager.instance().tokens
        self.setStyleSheet(f"""
            QLabel#homeTitle {{
                font-size: {FONT_SIZE["heading"]}px;
                font-weight: 700;
                color: {t.text};
            }}
            QLabel#sectionSubtitle, QLabel#detailTitle {{
                font-size: {FONT_SIZE["body"]}px;
                color: {t.text};
                font-weight: 600;
            }}
            QLabel#detailTitle {{
                font-size: {FONT_SIZE["title"]}px;
            }}
            QLabel#mutedLabel, QLabel#tagLabel {{
                font-size: {FONT_SIZE["small"]}px;
                color: {t.muted};
            }}
            QLabel#tagLabel {{
                color: {t.primary};
            }}
            QListWidget {{
                border: 1px solid {t.border};
                border-radius: {RADIUS["sm"]}px;
                padding: 4px;
                background: {t.surface};
            }}
            QListWidget::item {{
                padding: 6px 8px;
                border-radius: 4px;
            }}
            QListWidget::item:selected {{
                background: {t.primary}22;
                color: {t.text};
            }}
            QTextEdit {{
                border: 1px solid {t.border};
                border-radius: {RADIUS["sm"]}px;
                padding: 8px;
                background: {t.surface};
                font-family: {FONT_FAMILY_MONO};
                font-size: {FONT_SIZE["small"]}px;
            }}
        """)

    # ── 生命周期 ──────────────────────────────────────────────
    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        if not self._auto_loaded:
            self._auto_loaded = True
            self.refresh()

    # ── 拉取目录 ──────────────────────────────────────────────
    def refresh(self) -> None:
        if not self._catalog_url and not self._bundled_catalog_dir and not self._local_fallback.is_dir():
            self._set_offline_state(_("未配置 catalog_url，且无本地 OmniCrawler-market/ 回退。"))
            return
        self._state = "loading"
        self._status_indicator.state = "running"
        self._status_label.setText(_("正在拉取插件目录..."))
        self._footer.setText(_("正在连接插件目录..."))
        self._refresh_btn.setEnabled(False)

        catalog_url = self._catalog_url or (self._bundled_catalog_dir or str(self._local_fallback))
        self._catalog_worker = _CatalogWorker(catalog_url, self._local_fallback, self._egress, parent=self)
        self._catalog_worker.succeeded.connect(self._on_catalog_loaded)
        self._catalog_worker.failed.connect(self._on_catalog_error)
        self._catalog_worker.finished.connect(self._catalog_worker.deleteLater)
        self._catalog_worker.start()

    def _on_catalog_loaded(self, catalog: dict[str, Any]) -> None:
        self._catalog = catalog
        self._state = "ready"
        self._status_indicator.state = "finished"
        source = catalog.get("_source", self._catalog_url)
        self._status_label.setText(_("已连接"))
        # 截断显示来源，避免过长挤占布局
        shown = source if len(source) <= 64 else "…" + source[-62:]
        self._source_label.setText(shown)
        self._footer.setText(_(f"共 {len(catalog.get('plugins', []))} 个已审核插件。"))
        self._refresh_btn.setEnabled(True)
        self._populate_list()

    def _on_catalog_error(self, msg: str) -> None:
        self._state = "offline"
        self._status_indicator.state = "error"
        self._status_label.setText(_("离线"))
        self._source_label.setText("")
        self._footer.setText(
            _("无法连接插件目录（可能离线）：{0}。已安装插件仍可使用；联网后点「刷新」重试。").format(
                msg.split(chr(10))[0]
            )
        )
        self._refresh_btn.setEnabled(True)
        # 离线也展示已安装列表，便于管理
        self._populate_list()

    # ── 列表与详情 ────────────────────────────────────────────
    def _populate_list(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        plugins = (self._catalog or {}).get("plugins", []) if self._catalog else []
        for entry in plugins:
            pid = entry.get("id", "")
            name = entry.get("name", pid)
            version = entry.get("version", "")
            installed = self._is_installed(pid)
            label = f"{name}  v{version}" if version else name
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, pid)
            if installed:
                item.setText(f"✓ {label}")
            item.setToolTip(pid)
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
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _on_selection_changed(self, current, _previous) -> None:
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
        tags = (entry or {}).get("tags", [])
        summary = (entry or {}).get("summary", "")

        self._detail_name.setText(name)
        meta_parts = [
            f"v{version}" if version else "",
            publisher,
            category,
            _(f"兼容 {compat}") if compat else "",
            license_,
        ]
        self._detail_meta.setText(" · ".join(p for p in meta_parts if p))
        self._detail_tags.setText(_("标签: ") + (", ".join(tags) if tags else "—"))
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

    # ── 安装 / 卸载 / 校验 ────────────────────────────────────
    def _on_install(self) -> None:
        pid = self._selected_id
        if not pid or self._state != "ready":
            ToastManager.instance().warning(_("请先联网刷新并选择插件"))
            return
        self._install_btn.setEnabled(False)
        self._footer.setText(_(f"正在下载并校验 {pid} ..."))
        self._install_worker = _InstallWorker(
            pid, self._catalog_url, self._dest_root, self._trust_source, self._egress, parent=self
        )
        self._install_worker.succeeded.connect(self._on_installed)
        self._install_worker.failed.connect(self._on_install_error)
        self._install_worker.finished.connect(self._install_worker.deleteLater)
        self._install_worker.start()

    def _on_installed(self, plugin_id: str) -> None:
        ToastManager.instance().success(_(f"已安装并校验通过：{plugin_id}"))
        self._footer.setText(_(f"已安装 {plugin_id} 到 {self._dest_root / plugin_id}"))
        self._populate_list()
        self._update_action_buttons(installed=True)
        self._prompt_p2p_trust(plugin_id)

    def _open_identity_dialog(self) -> None:
        from .identity_dialog import IdentityDialog

        dialog = IdentityDialog(parent=self)
        dialog.exec()

    def _prompt_p2p_trust(self, plugin_id: str) -> None:
        """安装的插件仅带创作者签名（无维护者签名）时，询问是否信任该创作者。"""
        from PyQt6.QtWidgets import QMessageBox

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

    def _on_uninstall(self) -> None:
        from PyQt6.QtWidgets import QMessageBox

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

    # ── 辅助 ───────────────────────────────────────────────────
    def _is_installed(self, plugin_id: str) -> bool:
        target = self._dest_root / plugin_id
        return (target / "plugin.py").is_file() and (target / "plugin.py.sig").is_file()

    def _installed_ids(self) -> list[str]:
        if not self._dest_root.is_dir():
            return []
        return [d.name for d in self._dest_root.iterdir() if d.is_dir() and (d / "plugin.py.sig").is_file()]

    def _entry_of(self, plugin_id: str) -> dict[str, Any] | None:
        if not self._catalog:
            return None
        for entry in self._catalog.get("plugins", []):
            if entry.get("id") == plugin_id:
                return entry
        return None

    def _update_action_buttons(self, installed: bool | None = None) -> None:
        pid = self._selected_id
        if pid is None:
            self._install_btn.setEnabled(False)
            self._uninstall_btn.setEnabled(False)
            self._verify_btn.setEnabled(False)
            return
        if installed is None:
            installed = self._is_installed(pid)
        can_network = self._state == "ready"
        self._install_btn.setEnabled(can_network and not installed)
        self._install_btn.setText(_("重装") if installed else _("安装"))
        self._uninstall_btn.setEnabled(installed)
        self._verify_btn.setEnabled(installed)

    def _set_offline_state(self, message: str) -> None:
        self._state = "offline"
        self._status_indicator.state = "error"
        self._status_label.setText(_("离线"))
        self._footer.setText(message)
        self._refresh_btn.setEnabled(True)
        self._populate_list()
