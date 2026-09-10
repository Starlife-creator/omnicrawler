"""PluginMarketView 的「目录加载」域 —— 由 plugin_market.py 抽出的 Mixin（P1-3 第六批）。

涵盖插件目录的加载编排与终态收尾：
- ``showEvent`` / ``refresh``：首次展示自动加载 + 手动刷新（启动 _CatalogWorker）
- ``_on_catalog_loaded`` / ``_on_catalog_error``：目录终态收尾（成功→填充列表；失败→离线态）
- ``_set_offline_state``：离线态复位（禁用联网操作、仅展示已安装）

以 Mixin 形式保留 ``self`` 语义，宿主属性与全部调用点不变。
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..i18n import _
from .plugin_market_workers import _CatalogWorker

if TYPE_CHECKING:
    from PySide6.QtWidgets import QLabel, QPushButton, QWidget

    from ..widgets.status_indicator import StatusIndicator

    # 类型检查期把宿主视作 QWidget；运行期仍为 object，不改变 PluginMarketView 的 MRO。
    _Base = QWidget
else:
    _Base = object


class MarketCatalogMixin(_Base):
    """目录加载域：目录拉取编排、终态收尾与离线态。"""

    # ---- 宿主契约：实例属性 ----
    _state: str
    _auto_loaded: bool
    _base: Path
    _local_fallback: Path
    _catalog: dict[str, Any] | None
    _catalog_url: str
    _bundled_catalog_dir: str
    _trust_source: str
    _egress: Any
    _catalog_worker: _CatalogWorker | None
    _status_indicator: StatusIndicator
    _status_label: QLabel
    _source_label: QLabel
    _refresh_btn: QPushButton
    _footer: QLabel

    # ---- 宿主契约：方法（由 MarketBrowseMixin 提供）----
    if TYPE_CHECKING:
        def _populate_list(self) -> None: ...
    def showEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        if not self._auto_loaded:
            self._auto_loaded = True
            self.refresh()

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
        self._catalog_worker = _CatalogWorker(
            catalog_url,
            self._local_fallback,
            self._trust_source,
            self._base / ".omnicrawler" / "catalog-cache",
            self._egress,
            parent=self,
        )
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

    def _set_offline_state(self, message: str) -> None:
        self._state = "offline"
        self._status_indicator.state = "error"
        self._status_label.setText(_("离线"))
        self._footer.setText(message)
        self._refresh_btn.setEnabled(True)
        self._populate_list()
