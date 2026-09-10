"""插件子系统门面：保持 ``omnicrawler.plugins.plugins`` 导入路径稳定。

实现已按职责拆分为六个姊妹模块——契约 plugin_contracts / 静态预检 plugin_preflight /
注册表 plugin_registry / 验签门 plugin_signature / 信任询问 plugin_trust_prompt /
加载器 plugin_loader。本模块不含实现，只做同名再导出（公开面见 ``__all__``）。
"""
from .plugin_contracts import (
    CORE_VERSION,
    MARKET_DIR_NAME,
    PLUGIN_API_VERSION,
    SUBPROCESS_ADAPTER_PLUGIN_TYPES,
    UI_PERMISSIONS,
    BackgroundRegistration,
    PluginContext,
    PluginMetadata,
    StatusWidgetRegistration,
    ThemeRegistration,
    UIActionRegistration,
    UIPanelRegistration,
    _normalize_schema_fields,
)
from .plugin_contracts import (
    OFFICIAL_PLUGIN_TYPES as OFFICIAL_PLUGIN_TYPES,
)
from .plugin_loader import load_local_plugins
from .plugin_preflight import (
    _declared_creator_fingerprint,
    _decode_plugin_source,
    _permission_artifact_sha256,
    _permissions_from_metadata,
    _preflight_forbidden_patterns,
    _preflight_metadata,
    _resolve_plugin_permission_grant,
    _static_plugin_metadata,
)
from .plugin_registry import Factory, Registry
from .plugin_signature import (
    SIGNATURE_POLICIES,
    SIGNATURE_POLICY_DEVELOPER,
    SIGNATURE_POLICY_STRICT,
    _verify_plugin_signature,
)
from .plugin_trust_prompt import (
    TrustPrompter,
    TrustPromptResult,
    get_default_trust_prompter,
    set_default_trust_prompter,
)

__all__ = [
    "CORE_VERSION",
    "Factory",
    "MARKET_DIR_NAME",
    "OFFICIAL_PLUGIN_TYPES",
    "PLUGIN_API_VERSION",
    "SUBPROCESS_ADAPTER_PLUGIN_TYPES",
    "UI_PERMISSIONS",
    "BackgroundRegistration",
    "PluginContext",
    "PluginMetadata",
    "Registry",
    "SIGNATURE_POLICIES",
    "SIGNATURE_POLICY_DEVELOPER",
    "SIGNATURE_POLICY_STRICT",
    "StatusWidgetRegistration",
    "ThemeRegistration",
    "TrustPromptResult",
    "TrustPrompter",
    "UIActionRegistration",
    "UIPanelRegistration",
    "_declared_creator_fingerprint",
    "_decode_plugin_source",
    "_normalize_schema_fields",
    "_permission_artifact_sha256",
    "_permissions_from_metadata",
    "_preflight_forbidden_patterns",
    "_preflight_metadata",
    "_resolve_plugin_permission_grant",
    "_static_plugin_metadata",
    "_verify_plugin_signature",
    "get_default_trust_prompter",
    "load_local_plugins",
    "set_default_trust_prompter",
]
