"""插件契约叶子模块：版本常量、来源目录、权限族、官方扩展点、元数据模型与注册类型。

本模块是 plugins 包内的**叶子**（不导入任何包内模块），供 plugins.py（注册表/加载器）
与 plugin_preflight.py（静态预检）共同依赖，避免循环导入。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import __version__

PLUGIN_API_VERSION = 1
CORE_VERSION = __version__

# 市场安装目录名：位于**项目根下该目录**的插件视为市场来源
# （维护者签名+信任根门禁）。市场来源判定必须是「规范的安装位置」，
# 而不是"路径里碰巧出现这个名字"——后者可被任意目录名伪造
# （审查报告 B10：把市场插件挪进 plugins/ 即逃脱维护者签名要求）。
MARKET_DIR_NAME = "plugins_installed"

# UI 权限族：本地来源插件自动放行（GUI 插件宿主按注册类型挂载）；
# 市场来源插件仍需 permission_grants 按插件和载荷显式批准。
UI_PERMISSIONS = frozenset(
    {"ui:theme", "ui:action", "ui:panel", "ui:status", "ui:background"}
)

# 运行扩展点由宿主定义，不能由插件任意发明。业务分类与检索标签分别使用
# PluginMetadata.category / tags；二者不参与运行路由。
OFFICIAL_PLUGIN_TYPES = frozenset(
    {
        "source",
        "fetcher",
        "processor",
        "exporter",
        "auth_provider",
        "parser",
        "extractor",
        "transformer",
        "hook",
        "ui",
        "resource_provider",
        "view",
    }
)

# 当前契约 2 已具备并接入 subprocess adapter 的扩展点。
SUBPROCESS_ADAPTER_PLUGIN_TYPES = frozenset(
    {
        "source",
        "fetcher",
        "processor",
        "exporter",
        "auth_provider",
        "parser",
        "extractor",
        "transformer",
        "hook",
        "resource_provider",
        "view",
    }
)

@dataclass(frozen=True, slots=True)
class PluginMetadata:
    name: str
    version: str = "0.0.0"
    api_version: int = PLUGIN_API_VERSION
    description: str = ""
    plugin_types: tuple[str, ...] = ()
    # 市场业务分类与标签只用于展示/检索，不决定加载到哪个 Registry 槽位。
    category: str = ""
    tags: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
    # Contract 2 宿主能力协议的最低版本，例如 {"records.read": ">=1"}。
    # 它不同于 capabilities（展示用能力标签），会在启动子进程前 fail-closed。
    required_capabilities: dict[str, int | str] = field(default_factory=dict)
    state_schema_version: int = 1
    domains: tuple[str, ...] = ()
    config_schema: dict[str, Any] = field(default_factory=dict)
    permissions: tuple[str, ...] = ()
    optional_dependencies: tuple[str, ...] = ()
    license: str = ""
    source_url: str = ""
    min_core_version: str = "0.0.1"
    max_core_version: str = ""
    fallback: str = "generic"
    resource_limits: dict[str, Any] = field(default_factory=dict)
    # Phase 1（B1 schema 扩展）：执行模式声明（in_process|subprocess，
    # 缺省 subprocess 无兼容语义）+ 第三方依赖声明（门 3 双向一致性）
    execution_mode: str = "subprocess"
    dependencies: tuple[dict[str, Any], ...] = ()
    # files:read 路径白名单（第 82 轮更名：原 files 与市场仓扫描允许列表冲突）
    input_files: tuple[str, ...] = ()
    # Phase 3（B2）：契约形态（2=handle 契约 2 / 1=register 契约 1 / 0=未知）
    contract_shape: int = 2

@dataclass(frozen=True, slots=True)
class PluginContext:
    metadata: PluginMetadata
    network: Any | None = None

@dataclass(frozen=True, slots=True)
class ThemeRegistration:
    """UI 主题注册：覆盖 VisualTokens 令牌的色值（#RRGGBB/#RRGGBBAA 或 rgba()）。"""

    theme_id: str
    label: str
    tokens: dict[str, str]

@dataclass(frozen=True, slots=True)
class UIActionRegistration:
    """菜单动作注册：点击回调（可接受 mw 参数或空参）。"""

    action_id: str
    label: str
    callback: Callable[..., Any]
    section: str = "plugins"

@dataclass(frozen=True, slots=True)
class UIPanelRegistration:
    """侧栏面板注册：widget_factory 返回 QWidget。"""

    panel_id: str
    title: str
    widget_factory: Callable[..., Any]

@dataclass(frozen=True, slots=True)
class StatusWidgetRegistration:
    """状态栏小部件注册：widget_factory 返回 QWidget。"""

    widget_factory: Callable[..., Any]

@dataclass(frozen=True, slots=True)
class BackgroundRegistration:
    """声明式本地媒体背景；绘制、文件选择和多媒体生命周期均由宿主管理。"""

    background_id: str
    label: str
    default_opacity: float = 0.24
    default_dim: float = 0.30

def _normalize_schema_fields(result: PluginMetadata, path: Path) -> PluginMetadata:
    """Phase 1（B1 schema 扩展）：execution_mode 枚举归一 + 新字段类型收敛。

    - execution_mode 非法枚举 → 拒绝（无兼容语义）；未声明 = subprocess；
    - plugin_types 归一为宿主受控的小写扩展点；未知类型拒绝；
    - tags / dependencies / input_files 收敛为 tuple（兼容插件以 list 声明）。
    """
    mode = str(result.execution_mode or "").strip()
    if mode == "":
        mode = "subprocess"
    if mode not in ("in_process", "subprocess"):
        raise ValueError(
            f"插件 {result.name} execution_mode 非法: {mode!r}（仅 in_process | subprocess）; file={path}"
        )
    if not isinstance(result.plugin_types, (list, tuple)):
        raise ValueError(f"插件 {result.name} plugin_types 必须是列表或元组; file={path}")
    plugin_types = tuple(
        dict.fromkeys(str(item).strip().casefold() for item in result.plugin_types if str(item).strip())
    )
    unknown_types = set(plugin_types) - OFFICIAL_PLUGIN_TYPES
    if unknown_types:
        raise ValueError(
            f"插件 {result.name} 声明未知运行扩展点: {sorted(unknown_types)}；"
            "自定义业务分类请使用 category/tags，能力名称请使用 capabilities; "
            f"file={path}"
        )
    tags = result.tags
    if isinstance(tags, str) or not isinstance(tags, (list, tuple)):
        raise ValueError(f"插件 {result.name} tags 必须是列表或元组; file={path}")
    if not isinstance(tags, tuple):
        tags = tuple(str(item) for item in tags)
    deps = result.dependencies
    if not isinstance(deps, tuple):
        deps = tuple(deps)
    input_files = result.input_files
    if not isinstance(input_files, tuple):
        input_files = tuple(str(item) for item in input_files)
    required_capabilities = result.required_capabilities
    if not isinstance(required_capabilities, dict):
        raise ValueError(f"插件 {result.name} required_capabilities 必须是映射; file={path}")
    required_capabilities = {
        str(name).strip(): requirement for name, requirement in required_capabilities.items()
    }
    from .plugin_broker import validate_required_capabilities

    validate_required_capabilities(required_capabilities)
    if not isinstance(result.state_schema_version, int) or result.state_schema_version < 1:
        raise ValueError(f"插件 {result.name} state_schema_version 必须是正整数; file={path}")
    if (
        mode == result.execution_mode
        and plugin_types == result.plugin_types
        and str(result.category or "").strip() == result.category
        and tags is result.tags
        and deps is result.dependencies
        and input_files is result.input_files
        and required_capabilities == result.required_capabilities
    ):
        return result
    return PluginMetadata(
        name=result.name,
        version=result.version,
        api_version=result.api_version,
        description=result.description,
        plugin_types=plugin_types,
        category=str(result.category or "").strip(),
        tags=tags,
        capabilities=result.capabilities,
        required_capabilities=required_capabilities,
        state_schema_version=result.state_schema_version,
        domains=result.domains,
        config_schema=result.config_schema,
        permissions=result.permissions,
        optional_dependencies=result.optional_dependencies,
        license=result.license,
        source_url=result.source_url,
        min_core_version=result.min_core_version,
        max_core_version=result.max_core_version,
        fallback=result.fallback,
        resource_limits=result.resource_limits,
        execution_mode=mode,
        dependencies=deps,
        input_files=input_files,
        contract_shape=result.contract_shape,
    )
