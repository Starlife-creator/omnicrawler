"""插件市场面板的后台工作线程：目录拉取、列表资源下载与签名安装。

从 plugin_market.py 迁出（P1-3 第二批）。三者均继承 BackgroundWorker，
终态信号契约（succeeded/failed/interrupted 三选一）由基类保证。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

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
        parent=None,
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
