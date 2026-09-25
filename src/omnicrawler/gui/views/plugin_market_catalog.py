"""PluginMarketView 的「目录加载」域 —— 由 plugin_market.py 抽出的 Mixin（P1-3 第六批）。

涵盖插件目录的加载编排与终态收尾：
- ``showEvent`` / ``refresh``：首次展示自动加载 + 手动刷新（启动 _CatalogWorker）
- ``_on_catalog_loaded`` / ``_on_catalog_error``：目录终态收尾（成功→填充列表；失败→离线态）
- ``_set_offline_state``：离线态复位（禁用联网操作、仅展示已安装）

以 Mixin 形式保留 ``self`` 语义，宿主属性与全部调用点不变。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..i18n import _
from .plugin_market_workers import _CatalogWorker, _DependencyScanWorker

LOGGER = logging.getLogger(__name__)


def _reclaim_stale_staging(dest_root: Path) -> None:
    """Best-effort 回收中断安装遗留的暂存目录（#74 §5）。

    失败只记日志：回收是清理动作，绝不能因为它挡在市场加载前面。
    """
    try:
        from ...plugins.market_client import cleanup_stale_staging

        cleanup_stale_staging(dest_root)
    except Exception as exc:  # noqa: BLE001 - 清理失败不阻塞市场
        LOGGER.warning("stale staging reclaim skipped: %s", exc)

if TYPE_CHECKING:
    from PySide6.QtGui import QShowEvent
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
    _dest_root: Path
    _bundled_catalog_dir: str
    _trust_source: str
    _egress: Any
    _catalog_worker: _CatalogWorker | None
    _dependency_worker: _DependencyScanWorker | None
    _dependency_status: dict[str, Any]
    _status_indicator: StatusIndicator
    _status_label: QLabel
    _source_label: QLabel
    _refresh_btn: QPushButton
    _footer: QLabel

    # ---- 宿主契约：方法（由 MarketBrowseMixin 提供）----
    if TYPE_CHECKING:
        def _populate_list(self) -> None: ...
    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802 - Qt 命名
        super().showEvent(event)
        if not self._auto_loaded:
            self._auto_loaded = True
            self.refresh()

    def refresh(self) -> None:
        if not self._catalog_url and not self._bundled_catalog_dir and not self._local_fallback.is_dir():
            self._set_offline_state(_("未配置 catalog_url，且无本地 OmniCrawler-market/ 回退。"))
            return
        self._state = "loading"
        _reclaim_stale_staging(self._dest_root)
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

    def _on_local_install(self) -> None:
        """选择本地市场目录作为目录源（§十 P1 离线安装路径）。

        逻辑层 `download_and_verify` 原生支持本地目录作为 catalog 源
        （`_is_remote` 对非 http(s) 为 False，且有端到端测试覆盖），这里只做入口：
        校验所选目录含 catalog.json ⇒ 切换源 ⇒ 走既有 refresh 流程（不新增第二套安装实现）。
        """
        from PySide6.QtWidgets import QFileDialog

        from ..widgets.toast import ToastManager

        chosen = QFileDialog.getExistingDirectory(self, _("选择本地市场目录"))
        if not chosen:
            return
        root = Path(chosen)
        if not (root / "catalog.json").is_file():
            ToastManager.instance().warning(_("所选目录不含 catalog.json，不是有效的市场目录"))
            return
        self._catalog_url = str(root)
        self._footer.setText(_(f"已切换到本地市场目录：{root}"))
        self.refresh()

    def _on_switch_source(self) -> None:
        """多市场源体验（§10.2 #4）：输入 https 源 ⇒ 确认 ⇒ 切换并刷新。

        私网防线在 egress 层（已存在）；这里做入口侧校验（明文 http 拒绝）与
        明确的确认对话——切换来源是信任变更，必须用户点头。
        """
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        from ..widgets.toast import ToastManager
        from .plugin_market_logic import _validate_market_source

        url, ok = QInputDialog.getText(self, _("切换市场源"), _("输入 https:// 市场源地址："))
        if not ok or not url.strip():
            return
        error = _validate_market_source(url)
        if error:
            ToastManager.instance().warning(error)
            return
        reply = QMessageBox.question(
            self,
            _("确认切换市场源"),
            _("将把市场源切换为：\n{0}\n\n目录会立即刷新；新来源的目录同样经过签名校验（fail-closed）。确认继续？").format(url.strip()),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        self._catalog_url = url.strip()
        self._footer.setText(_(f"市场源已切换：{self._catalog_url}"))
        self.refresh()

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
        self._scan_dependency_status()
        self._populate_list()

    def _scan_dependency_status(self) -> None:
        """后台扫描已装插件的声明依赖（决策四：打开即检测，只读、不弹框）。

        只在真正装了插件时才启动线程（``plugins_installed/`` 不存在 ⇒ 无事可做）；
        结果回来只更新徽标（``_populate_list`` 重绘），齐全时静默——绝不打扰用户。
        """
        if not self._dest_root.is_dir():
            self._dependency_status = {}
            return
        worker = _DependencyScanWorker(self._dest_root, parent=self)
        worker.succeeded.connect(self._on_dependency_scanned)
        worker.finished.connect(worker.deleteLater)
        self._dependency_worker = worker
        worker.start()

    def _on_dependency_scanned(self, status: dict[str, Any]) -> None:
        self._dependency_status = dict(status or {})
        # 依赖徽标变化后重绘列表（保持选中/滚动由 _populate_list 自身负责）
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
        self._scan_dependency_status()
        self._populate_list()

    def _set_offline_state(self, message: str) -> None:
        self._state = "offline"
        self._status_indicator.state = "error"
        self._status_label.setText(_("离线"))
        self._footer.setText(message)
        self._refresh_btn.setEnabled(True)
        self._scan_dependency_status()
        self._populate_list()
