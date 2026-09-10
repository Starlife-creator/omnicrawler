"""插件市场面板 — 策展式插件分发 GUI。

联网后从 ``catalog_url`` 拉取审核通过的插件目录，展示名称/版本/说明，
用户按需下载安装；每份插件均经 ed25519 离线验签（fail-closed）后才落盘到
``plugins_installed/``。离线时仅展示已安装列表并禁用联网操作。安装不等于启用；新项目通过
``enabled_market_plugins`` 显式选择后，运行时才会加载。

设计约束（见 docs/ADR-001-plugin-catalog.md）：
- 仅联网时从远程拉取；远程失败可回退到本地 ``OmniCrawler-market/``（开发态便利）。
- 所有下载均用打包内的信任根公钥验签，绝不信任网络本身。
- 安装目录 ``plugins_installed/<id>/`` 位于默认扫描范围，但显式启用白名单仍是执行前置条件。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.config import DEFAULTS
from ...plugins.plugins import OFFICIAL_PLUGIN_TYPES
from ..design_system import FONT_FAMILY_MONO, FONT_SIZE, RADIUS, ThemeManager
from ..i18n import _
from ..widgets.status_indicator import StatusIndicator
from .plugin_market_actions import MarketActionsMixin
from .plugin_market_browse import MarketBrowseMixin
from .plugin_market_catalog import MarketCatalogMixin
from .plugin_market_install import MarketInstallMixin
from .plugin_market_logic import (
    _CATALOG_PURPOSE as _CATALOG_PURPOSE,
)
from .plugin_market_logic import (
    _TYPE_LABELS,
    _market_egress,
    _project_root_of,
)
from .plugin_market_logic import (
    _compatibility as _compatibility,
)
from .plugin_market_logic import (
    _entry_plugin_types as _entry_plugin_types,
)
from .plugin_market_logic import (
    _entry_strings as _entry_strings,
)
from .plugin_market_logic import (
    _install_block_reason as _install_block_reason,
)
from .plugin_market_logic import (
    _install_review_text as _install_review_text,
)
from .plugin_market_logic import (
    _permission_risk as _permission_risk,
)
from .plugin_market_logic import (
    _version_tuple as _version_tuple,
)
from .plugin_market_workers import _CatalogWorker, _InstallWorker, _ListingWorker

LOGGER = logging.getLogger(__name__)


# ── 后台任务 ─────────────────────────────────────────────────────


# ── 视图 ──────────────────────────────────────────────────────────
class PluginMarketView(
    MarketCatalogMixin, MarketInstallMixin, MarketBrowseMixin, MarketActionsMixin, QWidget
):
    """策展式插件市场面板。

    状态: offline | loading | ready | error
    """

    installation_completed = Signal(str)
    activation_requested = Signal(str)
    deactivation_requested = Signal(str)
    uninstall_completed = Signal(str)

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
        self._enabled_plugin_ids: set[str] = set()
        self._auto_loaded = False
        self._catalog_worker: _CatalogWorker | None = None
        self._listing_worker: _ListingWorker | None = None
        self._install_worker: _InstallWorker | None = None

        self._setup_ui()
        self._apply_style()
        ThemeManager.instance().theme_changed.connect(self._apply_style)

    @property
    def installed_root(self) -> Path:
        """Directory containing installed market plugins for this project."""
        return self._dest_root

    @property
    def trust_source(self) -> str:
        """Trust root used for installed market plugin verification."""
        return self._trust_source

    # ── UI 搭建 ────────────────────────────────────────────────
    def _setup_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(14)

        title = QLabel(_("市场"))
        title.setObjectName("homeTitle")
        root.addWidget(title)

        from .market_home import build_market_home_tabs
        from .template_market import TemplateMarketView

        self._tabs = QTabWidget()
        self._tabs.setObjectName("marketTabs")
        market_pane = QWidget()
        market_layout = QVBoxLayout(market_pane)
        market_layout.setContentsMargins(0, 0, 0, 0)
        market_tabs = QTabWidget()
        plugin_pane = QWidget()
        self._build_plugin_pane(plugin_pane)
        market_tabs.addTab(plugin_pane, _("插件"))
        self._template_market = TemplateMarketView(
            self._catalog_url, self._base, self._trust_source,
            bundled_catalog_dir=self._bundled_catalog_dir, parent=self
        )
        market_tabs.addTab(self._template_market, _("模板"))
        market_layout.addWidget(market_tabs)
        self._tabs.addTab(market_pane, _("市场"))
        build_market_home_tabs(self._base, self._tabs)
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

        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText(_("搜索名称、分类、标签或扩展点"))
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._populate_list)
        list_layout.addWidget(self._search_edit)

        filters = QHBoxLayout()
        self._type_filter = QComboBox()
        self._type_filter.addItem(_("全部类型"), "")
        for plugin_type in sorted(OFFICIAL_PLUGIN_TYPES):
            self._type_filter.addItem(_TYPE_LABELS.get(plugin_type, plugin_type), plugin_type)
        self._type_filter.currentIndexChanged.connect(self._populate_list)
        filters.addWidget(self._type_filter)
        self._mode_filter = QComboBox()
        self._mode_filter.addItem(_("全部模式"), "")
        self._mode_filter.addItem(_("隔离运行"), "subprocess")
        self._mode_filter.addItem(_("进程内运行"), "in_process")
        self._mode_filter.currentIndexChanged.connect(self._populate_list)
        filters.addWidget(self._mode_filter)
        self._risk_filter = QComboBox()
        self._risk_filter.addItem(_("全部风险"), "")
        self._risk_filter.addItem(_("低风险"), "low")
        self._risk_filter.addItem(_("需授权"), "medium")
        self._risk_filter.addItem(_("高风险"), "high")
        self._risk_filter.currentIndexChanged.connect(self._populate_list)
        filters.addWidget(self._risk_filter)
        list_layout.addLayout(filters)

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

        self._detail_capabilities = QLabel("")
        self._detail_capabilities.setObjectName("capabilityLabel")
        self._detail_capabilities.setWordWrap(True)
        detail_layout.addWidget(self._detail_capabilities)

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

        self._enable_btn = QPushButton(_("启用到当前项目"))
        self._enable_btn.clicked.connect(self._on_enable)
        btn_row.addWidget(self._enable_btn)

        self._disable_btn = QPushButton(_("在当前项目禁用"))
        self._disable_btn.clicked.connect(self._on_disable)
        btn_row.addWidget(self._disable_btn)

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
            QLabel#mutedLabel, QLabel#tagLabel, QLabel#capabilityLabel {{
                font-size: {FONT_SIZE["small"]}px;
                color: {t.muted};
            }}
            QLabel#tagLabel {{
                color: {t.primary};
            }}
            QLabel#capabilityLabel {{
                color: {t.text};
                padding: 6px 8px;
                border: 1px solid {t.border};
                border-radius: {RADIUS["sm"]}px;
                background: {t.nav};
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

    # ── 拉取目录 ──────────────────────────────────────────────


    # ── 列表与详情 ────────────────────────────────────────────


    # ── 安装 / 卸载 / 校验 ────────────────────────────────────


    # ── 辅助 ───────────────────────────────────────────────────


