"""模板市场面板 — 市场分发模板（对齐 Helios 三层体系：插件/模板/分块共享信任链）。

模板是声明式配置（YAML），经 ed25519 验签后安装到 ``templates_installed/<id>/``，
由 ``TemplateCatalog`` 的用户目录自动发现。签名/信任模型与插件市场一致：
维护者签名自动信任，创作者签名走信任列表（P2P 提示），未签名拒绝。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...plugins.market_client import (
    download_template_and_verify,
    fetch_catalog,
    fetch_resource,
    verify_installed_template,
)
from ..core.background_worker import BackgroundWorker
from ..design_system import FONT_FAMILY_MONO, FONT_SIZE, RADIUS, ThemeManager
from ..i18n import _
from ..widgets.status_indicator import StatusIndicator
from ..widgets.toast import ToastManager


def _market_egress(project_root: Path) -> Any:
    """Lazily build a shared EgressBroker for curated template-market traffic.

    B08-002 家族：与 plugin_market 对齐——模板目录下载/安装同样必须跨
    EgressBroker 的策略/预算/审计边界，而不是裸 urlopen。默认 broker 安全，
    因 egress 默认仅限制私网目标并计数请求。
    """
    from ...core.config import DEFAULTS, AppConfig, deep_merge
    from ...security.egress import EgressBroker

    raw = deep_merge(dict(DEFAULTS), {"egress": {"audit": True}})
    raw.setdefault("project", {"name": "template-market", "workspace": str(project_root)})
    config = AppConfig(Path("<template-market>"), project_root, raw, project_root)
    return EgressBroker(config)


class _TemplateCatalogWorker(BackgroundWorker):
    """后台拉取 catalog（模板页共用，仅读 templates 数组）。"""

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
                catalog = fetch_catalog(str(self._local_fallback))
                catalog["_source"] = str(self._local_fallback)
                return catalog
            raise


class _TemplateListingWorker(BackgroundWorker):
    def __init__(self, catalog_url: str, rel: str, egress: Any, parent=None) -> None:
        super().__init__(parent)
        self._catalog_url = catalog_url
        self._rel = rel
        self._egress = egress

    def work(self) -> str:
        return fetch_resource(self._catalog_url, self._rel, egress=self._egress).decode("utf-8", "replace")


class _TemplateInstallWorker(BackgroundWorker):
    def __init__(
        self,
        template_id: str,
        catalog_url: str,
        dest_root: Path,
        trust_source: str,
        egress: Any,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._template_id = template_id
        self._catalog_url = catalog_url
        self._dest_root = dest_root
        self._trust_source = trust_source
        self._egress = egress

    def work(self) -> str:
        download_template_and_verify(
            self._template_id,
            self._catalog_url,
            self._dest_root,
            self._trust_source,
            egress=self._egress,
        )
        return self._template_id


class TemplateMarketView(QWidget):
    """模板市场面板：列表 + 详情 + 安装/卸载/校验。"""

    def __init__(
        self,
        catalog_url: str,
        project_root: str | Path,
        trust_source: str,
        bundled_catalog_dir: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("templateMarket")
        self.setAccessibleName(_("模板市场"))

        self._base = Path(project_root)
        self._dest_root = self._base / "templates_installed"
        self._local_fallback = self._base.parent / "OmniCrawler-market"
        self._catalog_url = catalog_url
        self._bundled_catalog_dir = bundled_catalog_dir or ""
        self._trust_source = trust_source
        # B08-002 家族：共享 EgressBroker（对齐 plugin_market），模板目录/安装均跨出口审计。
        self._egress = _market_egress(self._base)

        self._state = "offline"
        self._catalog: dict[str, Any] | None = None
        self._selected_id: str | None = None
        self._auto_loaded = False
        self._catalog_worker: _TemplateCatalogWorker | None = None
        self._listing_worker: _TemplateListingWorker | None = None
        self._install_worker: _TemplateInstallWorker | None = None

        self._setup_ui()
        self._apply_style()
        ThemeManager.instance().theme_changed.connect(self._apply_style)

    # ── UI ───────────────────────────────────────────────
    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 12, 16, 12)
        root.setSpacing(10)

        top_bar = QHBoxLayout()
        self._status_indicator = StatusIndicator(size=12)
        top_bar.addWidget(self._status_indicator)
        self._status_label = QLabel(_("未连接"))
        self._status_label.setObjectName("mutedLabel")
        top_bar.addWidget(self._status_label)
        top_bar.addStretch(1)
        self._refresh_btn = QPushButton(_("刷新"))
        self._refresh_btn.clicked.connect(self.refresh)
        top_bar.addWidget(self._refresh_btn)
        root.addLayout(top_bar)

        splitter = QSplitter()
        splitter.setOrientation(Qt.Orientation.Horizontal)

        list_panel = QFrame()
        list_panel.setProperty("card", True)
        list_layout = QVBoxLayout(list_panel)
        list_layout.setContentsMargins(10, 10, 10, 10)
        list_header = QLabel(_("市场模板"))
        list_header.setObjectName("sectionSubtitle")
        list_layout.addWidget(list_header)
        self._list = QListWidget()
        self._list.setAlternatingRowColors(True)
        self._list.currentItemChanged.connect(self._on_selection_changed)
        list_layout.addWidget(self._list, 1)
        splitter.addWidget(list_panel)

        detail_panel = QFrame()
        detail_panel.setProperty("card", True)
        detail_layout = QVBoxLayout(detail_panel)
        detail_layout.setContentsMargins(12, 12, 12, 12)
        self._detail_name = QLabel(_("未选择模板"))
        self._detail_name.setObjectName("detailTitle")
        detail_layout.addWidget(self._detail_name)
        self._detail_meta = QLabel("")
        self._detail_meta.setObjectName("mutedLabel")
        self._detail_meta.setWordWrap(True)
        detail_layout.addWidget(self._detail_meta)
        self._detail_summary = QLabel("")
        self._detail_summary.setWordWrap(True)
        detail_layout.addWidget(self._detail_summary)
        self._detail_listing = QTextEdit()
        self._detail_listing.setReadOnly(True)
        self._detail_listing.setMinimumHeight(140)
        detail_layout.addWidget(self._detail_listing, 1)

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

        self._footer = QLabel(_("安装后由模板库自动发现（templates_installed/）。"))
        self._footer.setObjectName("mutedLabel")
        self._footer.setWordWrap(True)
        root.addWidget(self._footer)

        self._update_action_buttons()

    def _apply_style(self, *_args: Any) -> None:
        t = ThemeManager.instance().tokens
        self.setStyleSheet(f"""
            QLabel#sectionSubtitle, QLabel#detailTitle {{
                font-size: {FONT_SIZE["body"]}px;
                color: {t.text};
                font-weight: 600;
            }}
            QLabel#detailTitle {{ font-size: {FONT_SIZE["title"]}px; }}
            QLabel#mutedLabel {{ font-size: {FONT_SIZE["small"]}px; color: {t.muted}; }}
            QListWidget {{
                border: 1px solid {t.border};
                border-radius: {RADIUS["sm"]}px;
                padding: 4px;
                background: {t.surface};
            }}
            QListWidget::item {{ padding: 6px 8px; border-radius: 4px; }}
            QListWidget::item:selected {{ background: {t.primary}22; color: {t.text}; }}
            QTextEdit {{
                border: 1px solid {t.border};
                border-radius: {RADIUS["sm"]}px;
                padding: 8px;
                background: {t.surface};
                font-family: {FONT_FAMILY_MONO};
                font-size: {FONT_SIZE["small"]}px;
            }}
        """)

    # ── 生命周期 ────────────────────────────────────────
    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        if not self._auto_loaded:
            self._auto_loaded = True
            self.refresh()

    # ── 数据 ────────────────────────────────────────────
    def refresh(self) -> None:
        catalog_url = self._catalog_url or (self._bundled_catalog_dir or str(self._local_fallback))
        self._state = "loading"
        self._status_indicator.state = "running"
        self._status_label.setText(_("正在拉取..."))
        self._refresh_btn.setEnabled(False)
        self._catalog_worker = _TemplateCatalogWorker(
            catalog_url, self._local_fallback, self._egress, parent=self
        )
        self._catalog_worker.succeeded.connect(self._on_catalog_loaded)
        self._catalog_worker.failed.connect(self._on_catalog_error)
        self._catalog_worker.finished.connect(self._catalog_worker.deleteLater)
        self._catalog_worker.start()

    def _on_catalog_loaded(self, catalog: dict[str, Any]) -> None:
        self._catalog = catalog
        self._state = "ready"
        self._status_indicator.state = "finished"
        self._status_label.setText(_("已连接"))
        self._refresh_btn.setEnabled(True)
        self._populate_list()

    def _on_catalog_error(self, msg: str) -> None:
        self._state = "offline"
        self._status_indicator.state = "error"
        self._status_label.setText(_("离线"))
        self._refresh_btn.setEnabled(True)
        self._footer.setText(_("无法连接模板目录：{0}").format(msg.split(chr(10))[0]))
        self._populate_list()

    def _populate_list(self) -> None:
        self._list.blockSignals(True)
        self._list.clear()
        templates = (self._catalog or {}).get("templates", []) if self._catalog else []
        for entry in templates:
            tid = entry.get("id", "")
            name = entry.get("name", tid)
            version = entry.get("version", "")
            installed = self._is_installed(tid)
            label = f"{name}  v{version}" if version else name
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, tid)
            if installed:
                item.setText(f"✓ {label}")
            item.setToolTip(tid)
            self._list.addItem(item)
        self._list.blockSignals(False)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _on_selection_changed(self, current, _previous) -> None:
        if current is None:
            return
        self._show_detail(current.data(Qt.ItemDataRole.UserRole))

    def _show_detail(self, template_id: str | None) -> None:
        self._selected_id = template_id
        if not template_id:
            self._detail_name.setText(_("未选择模板"))
            self._detail_meta.setText("")
            self._detail_summary.setText("")
            self._detail_listing.setText("")
            self._update_action_buttons()
            return
        entry = self._entry_of(template_id)
        name = (entry or {}).get("name", template_id)
        version = (entry or {}).get("version", "")
        publisher = (entry or {}).get("publisher", "")
        category = (entry or {}).get("category", "")
        compat = (entry or {}).get("compatible_core", "")
        summary = (entry or {}).get("summary", "")
        self._detail_name.setText(name)
        self._detail_meta.setText(" · ".join(p for p in [f"v{version}", publisher, category, compat] if p))
        self._detail_summary.setText(summary)
        self._detail_listing.setText(
            _(
                "（加载功能说明中...）"
                if entry and entry.get("description_file")
                else "（该模板未提供功能说明）"
            )
        )
        self._update_action_buttons()

        if entry and entry.get("description_file"):
            source = (self._catalog or {}).get("_source", self._catalog_url)
            if source:
                self._listing_worker = _TemplateListingWorker(
                    source, entry["description_file"], self._egress, parent=self
                )
                self._listing_worker.succeeded.connect(
                    lambda text, tid=template_id: self._on_listing_loaded(tid, text)
                )
                self._listing_worker.failed.connect(lambda _e, tid=template_id: self._on_listing_error(tid))
                self._listing_worker.finished.connect(self._listing_worker.deleteLater)
                self._listing_worker.start()

    def _on_listing_loaded(self, template_id: str, text: str) -> None:
        if template_id == self._selected_id:
            self._detail_listing.setText(text)

    def _on_listing_error(self, template_id: str) -> None:
        if template_id == self._selected_id:
            self._detail_listing.setText(_("（功能说明加载失败）"))

    # ── 操作 ────────────────────────────────────────────
    def _on_install(self) -> None:
        tid = self._selected_id
        if not tid or self._state != "ready":
            ToastManager.instance().warning(_("请先联网刷新并选择模板"))
            return
        self._install_btn.setEnabled(False)
        self._footer.setText(_(f"正在下载并校验 {tid} ..."))
        self._install_worker = _TemplateInstallWorker(
            tid, self._catalog_url, self._dest_root, self._trust_source, self._egress, parent=self
        )
        self._install_worker.succeeded.connect(self._on_installed)
        self._install_worker.failed.connect(self._on_install_error)
        self._install_worker.finished.connect(self._install_worker.deleteLater)
        self._install_worker.start()

    def _on_installed(self, template_id: str) -> None:
        ToastManager.instance().success(_(f"已安装并校验通过：{template_id}"))
        self._footer.setText(_(f"已安装 {template_id}（templates_installed/ 将被模板库自动发现）"))
        self._populate_list()
        self._update_action_buttons()

    def _on_install_error(self, msg: str) -> None:
        ToastManager.instance().error(_(f"安装失败：{msg.split(chr(10))[0]}"))
        self._footer.setText(_(f"安装失败：{msg.split(chr(10))[0]}"))
        self._update_action_buttons()

    def _on_uninstall(self) -> None:
        tid = self._selected_id
        if not tid or not self._is_installed(tid):
            ToastManager.instance().warning(_("未选择已安装模板"))
            return
        reply = QMessageBox.question(
            self,
            _("卸载模板"),
            _(f"确定卸载模板 {tid}？\n将从 {self._dest_root / tid} 移除。"),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        import shutil

        shutil.rmtree(self._dest_root / tid, ignore_errors=True)
        ToastManager.instance().success(_(f"已卸载：{tid}"))
        self._populate_list()
        self._update_action_buttons()

    def _on_verify(self) -> None:
        tid = self._selected_id
        if not tid or not self._is_installed(tid):
            ToastManager.instance().warning(_("未选择已安装模板"))
            return
        ok, reason = verify_installed_template(self._dest_root, tid, self._trust_source)
        (ToastManager.instance().success if ok else ToastManager.instance().error)(
            _(f"{tid} 模板校验{'通过' if ok else '失败'}：{reason}")
        )

    # ── 辅助 ────────────────────────────────────────────
    def _is_installed(self, template_id: str) -> bool:
        target = self._dest_root / template_id
        return (target / "template.yaml").is_file() and (target / "template.yaml.sig").is_file()

    def _entry_of(self, template_id: str) -> dict[str, Any] | None:
        if not self._catalog:
            return None
        for entry in self._catalog.get("templates", []):
            if entry.get("id") == template_id:
                return entry
        return None

    def _update_action_buttons(self) -> None:
        tid = self._selected_id
        installed = self._is_installed(tid) if tid else False
        can_network = self._state == "ready"
        self._install_btn.setEnabled(bool(tid) and can_network and not installed)
        self._install_btn.setText(_("重装") if installed else _("安装"))
        self._uninstall_btn.setEnabled(installed)
        self._verify_btn.setEnabled(installed)
