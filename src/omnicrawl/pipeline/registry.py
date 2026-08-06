"""组件注册表构建。"""

from __future__ import annotations

from ..core.config import AppConfig
from ..extraction import extractors
from ..plugins.plugins import Registry, load_local_plugins
from ..security.egress import EgressBroker
from ..sources import site_adapters, sources
from . import exporters


def build_registry(
    config: AppConfig | None = None,
    egress: EgressBroker | None = None,
) -> Registry:
    """注册所有内置组件并按配置加载本地插件。

    S4.1 ⑥：重量级 fetcher（浏览器/异步）延迟到本函数内导入——
    import pipeline 与构建纯 HTTP 注册表不全量加载浏览器栈。
    """
    from ..fetching import async_fetcher, browser_fetcher
    from ..fetching.http_client import HTTPFetcher

    registry = Registry()
    sources.register(registry)
    site_adapters.register(registry)
    extractors.register(registry)
    browser_fetcher.register(registry)
    async_fetcher.register(registry)
    exporters.register(registry)
    registry.register_fetcher("http", HTTPFetcher)
    if config:
        paths = [str(item) for item in config.section("plugins").get("paths", [])]
        load_local_plugins(
            registry,
            paths,
            config.root,
            allow_external_paths=bool(config.section("plugins").get("allow_external_paths", False)),
            fail_open=bool(config.section("plugins").get("fail_open", False)),
            approved_permissions=tuple(
                str(item) for item in config.section("plugins").get("approved_permissions", [])
            ),
            config=config,
            egress=egress,
        )
    return registry
