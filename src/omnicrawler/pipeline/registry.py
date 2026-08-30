"""组件注册表构建。"""

from __future__ import annotations

from ..core.config import AppConfig
from ..extraction import extractors
from ..plugins.plugins import (
    SIGNATURE_POLICIES,
    SIGNATURE_POLICY_STRICT,
    Registry,
    TrustPrompter,
    get_default_trust_prompter,
    load_local_plugins,
)
from ..security.egress import EgressBroker
from ..sources import site_adapters, sources
from . import exporters


def build_registry(
    config: AppConfig | None = None,
    egress: EgressBroker | None = None,
    trust_prompter: TrustPrompter | None = None,
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
        plugins_config = config.section("plugins")
        paths = [str(item) for item in plugins_config.get("paths", [])]
        policy = str(plugins_config.get("signature_policy", SIGNATURE_POLICY_STRICT))
        if policy not in SIGNATURE_POLICIES:
            raise ValueError(f"plugins.signature_policy 非法: {policy}")
        if trust_prompter is None:
            trust_prompter = get_default_trust_prompter()
        load_local_plugins(
            registry,
            paths,
            config.root,
            allow_external_paths=bool(plugins_config.get("allow_external_paths", False)),
            fail_open=bool(plugins_config.get("fail_open", False)),
            approved_permissions=tuple(str(item) for item in plugins_config.get("approved_permissions", [])),
            permission_grants=(
                dict(plugins_config.get("permission_grants", {}))
                if plugins_config.get("permission_grants")
                else None if plugins_config.get("approved_permissions") else {}
            ),
            enabled_market_plugins=(
                {
                    str(item)
                    for item in plugins_config.get("enabled_market_plugins", [])
                }
                if plugins_config.get("enabled_market_plugins") is not None
                else None
            ),
            ast_allowed_patterns=tuple(
                str(item) for item in plugins_config.get("ast_allowed_patterns", [])
            ),
            signature_policy=policy,
            trust_prompter=trust_prompter,
            config=config,
            egress=egress,
        )
    return registry
