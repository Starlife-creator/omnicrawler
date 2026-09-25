"""插件市场面板的后台工作线程：目录拉取、列表资源下载与签名安装。

从 plugin_market.py 迁出（P1-3 第二批）。三者均继承 BackgroundWorker，
终态信号契约（succeeded/failed/interrupted 三选一）由基类保证。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QWidget

from ...plugins.market_client import (
    catalog_cache_path,
    download_and_verify,
    fetch_catalog_verified,
    fetch_resource,
)
from ..core.background_worker import BackgroundWorker


class _CatalogWorker(BackgroundWorker):
    """后台拉取 catalog：先远程，失败回退本地 OmniCrawler-market/。"""

    def __init__(
        self,
        catalog_url: str,
        local_fallback: Path,
        trust_source: str,
        cache_root: Path,
        egress: Any,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._catalog_url = catalog_url
        self._local_fallback = local_fallback
        self._trust_source = trust_source
        self._cache_root = cache_root
        self._egress = egress

    def work(self) -> dict[str, Any]:
        try:
            catalog = fetch_catalog_verified(
                self._catalog_url,
                self._trust_source,
                cache_path=catalog_cache_path(self._cache_root, self._catalog_url),
                egress=self._egress,
            )
            catalog["_source"] = self._catalog_url
            return catalog
        except Exception:
            if self._local_fallback.is_dir():
                local = str(self._local_fallback)
                catalog = fetch_catalog_verified(
                    local,
                    self._trust_source,
                    cache_path=catalog_cache_path(self._cache_root, local),
                )
                catalog["_source"] = local
                return catalog
            raise

class _ListingWorker(BackgroundWorker):
    """后台拉取单个插件的 listing.md 说明。"""

    def __init__(
        self, catalog_url: str, rel: str, egress: Any, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._catalog_url = catalog_url
        self._rel = rel
        self._egress = egress

    def work(self) -> str:
        return fetch_resource(self._catalog_url, self._rel, egress=self._egress).decode("utf-8", "replace")

class _DependencyScanWorker(BackgroundWorker):
    """后台扫描已装插件的**声明依赖**可用性（决策四：打开即检测，不冻结 UI）。

    只读：只对 ``plugin.yaml`` 的 ``dependencies`` 做 ``find_spec`` 探测，
    不执行插件代码、不联网、不安装（安装由用户点击触发，见 dependency_dialog）。
    """

    def __init__(self, plugins_root: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._plugins_root = plugins_root

    def work(self) -> dict[str, Any]:
        from ...plugins.plugin_dependency_check import plugin_dependency_status_for_root

        # 返回 {plugin_id: DependencyStatus}；dataclass 跨线程传递安全（不可变）
        return plugin_dependency_status_for_root(self._plugins_root)


class InstallError(Exception):
    """携带结构化原因链的安装失败；`str()` 即 JSON（便于经 `failed(str)` 信号跨线程）。

    由来：`BackgroundWorker.run` 对异常只发 `str(exc)`，GUI 层此前只能拿到第一行；
    P0 要求失败给**原因链** ⇒ 在线程边界把链序列化，主线程再解析还原。
    """

    def __init__(self, chain: dict[str, Any]) -> None:
        self.chain = chain
        super().__init__(json.dumps(chain, ensure_ascii=False))


class _InstallWorker(BackgroundWorker):
    """后台下载 + ed25519 验签 + 落盘安装。"""

    def __init__(
        self,
        plugin_id: str,
        catalog_url: str,
        dest_root: Path,
        trust_source: str,
        egress: Any,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._plugin_id = plugin_id
        self._catalog_url = catalog_url
        self._dest_root = dest_root
        self._trust_source = trust_source
        self._egress = egress

    def work(self) -> str:
        from .plugin_market_logic import install_failure_chain

        try:
            download_and_verify(
                self._plugin_id,
                self._catalog_url,
                self._dest_root,
                self._trust_source,
                egress=self._egress,
            )
        except Exception as exc:  # noqa: BLE001 - 在线程边界把原因链结构化打包
            raise InstallError(install_failure_chain(exc)) from exc
        return self._plugin_id
