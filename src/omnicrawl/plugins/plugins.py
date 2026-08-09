from __future__ import annotations

import ast
import importlib.util
import inspect
import logging
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .. import __version__
from ..core.config import AppConfig
from ..security.egress import EgressBroker
from . import signing

Factory = Callable[..., Any]
PLUGIN_API_VERSION = 1
CORE_VERSION = __version__
LOGGER = logging.getLogger(__name__)

# 市场安装目录名：位于该目录下的插件视为市场来源（维护者签名+信任根门禁）。
# 其余路径（默认 plugins/）视为本地来源（创作者签名+信任询问门禁）。
MARKET_DIR_NAME = "plugins_installed"

# UI 权限族：本地来源插件自动放行（GUI 插件宿主按注册类型挂载）；
# 市场来源插件仍需 approved_permissions 显式批准。
UI_PERMISSIONS = frozenset({"ui:theme", "ui:action", "ui:panel", "ui:status"})

SIGNATURE_POLICY_STRICT = "strict"
SIGNATURE_POLICY_DEVELOPER = "developer"
SIGNATURE_POLICIES = (SIGNATURE_POLICY_STRICT, SIGNATURE_POLICY_DEVELOPER)


class TrustPromptResult(Enum):
    """信任询问结果：信任并加入列表 / 仅本次加载 / 拒绝。"""

    TRUST_AND_LOAD = "trust_and_load"
    LOAD_ONCE = "load_once"
    REJECT = "reject"


# 信任询问器：参数 (plugin_id, author_username, fingerprint)，返回决策；
# 返回 None 表示调用方无交互能力（按拒绝处理）。
TrustPrompter = Callable[[str, str, str], TrustPromptResult | None]

_default_trust_prompter: TrustPrompter | None = None


def set_default_trust_prompter(prompter: TrustPrompter | None) -> None:
    """设置进程级默认信任询问器（CLI/GUI 入口调用一次）。

    未设置时为 None：strict 策略下未信任作者的本地插件直接拒载
    （后台任务、worker、无人值守场景不弹交互）。
    """
    global _default_trust_prompter  # noqa: PLW0603
    _default_trust_prompter = prompter


def get_default_trust_prompter() -> TrustPrompter | None:
    return _default_trust_prompter

# S2.5.41：插件模块加载缓存——同一文件（mtime 未变）只 exec_module 一次，
# 多 Pipeline 不再重复编译执行插件代码。键 = (路径, mtime_ns)。
_PLUGIN_MODULE_CACHE: dict[tuple[Path, int], Any] = {}
_PLUGIN_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True, slots=True)
class PluginMetadata:
    name: str
    version: str = "0.0.0"
    api_version: int = PLUGIN_API_VERSION
    description: str = ""
    plugin_types: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()
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


def _metadata(module: Any, path: Path) -> PluginMetadata:
    value = getattr(module, "PLUGIN_METADATA", None)
    if value is None:
        return PluginMetadata(path.stem, description="legacy plugin")
    if isinstance(value, PluginMetadata):
        result = value
    elif isinstance(value, dict):
        result = PluginMetadata(**value)
    else:
        raise TypeError(f"PLUGIN_METADATA必须是PluginMetadata或字典: {path}")
    if result.api_version != PLUGIN_API_VERSION:
        raise RuntimeError(f"插件API版本不兼容: {path} 需要{result.api_version}，当前为{PLUGIN_API_VERSION}")
    if not result.name.strip():
        raise ValueError(f"插件名称不能为空: {path}")
    if _version(result.min_core_version) > _version(CORE_VERSION):
        raise RuntimeError(f"Plugin {result.name} requires OmniCrawler >= {result.min_core_version}")
    if result.max_core_version and _version(result.max_core_version) < _version(CORE_VERSION):
        raise RuntimeError(f"Plugin {result.name} supports OmniCrawler <= {result.max_core_version}")
    return result


def _version(value: str) -> tuple[int, ...]:
    parts = []
    for token in value.split("."):
        digits = "".join(char for char in token if char.isdigit())
        parts.append(int(digits or 0))
    return tuple(parts)


class Registry:
    def __init__(self) -> None:
        self.sources: dict[str, Factory] = {}
        self.fetchers: dict[str, Factory] = {}
        self.processors: dict[str, Factory] = {}
        self.exporters: dict[str, Factory] = {}
        self.auth_providers: dict[str, Factory] = {}
        self.parsers: dict[str, Factory] = {}
        self.extractors: dict[str, Factory] = {}
        self.transformers: dict[str, Factory] = {}
        self.hooks: dict[str, list[Factory]] = {}
        self.themes: dict[str, ThemeRegistration] = {}
        self.ui_actions: dict[str, UIActionRegistration] = {}
        self.ui_panels: dict[str, UIPanelRegistration] = {}
        self.status_widgets: list[StatusWidgetRegistration] = []
        self.plugins: list[PluginMetadata] = []
        self.plugin_errors: list[dict[str, str]] = []
        self._error_lock = threading.Lock()

    def register_source(self, name: str, factory: Factory) -> None:
        self._register(self.sources, name, factory)

    def register_fetcher(self, name: str, factory: Factory) -> None:
        self._register(self.fetchers, name, factory)

    def register_processor(self, name: str, factory: Factory) -> None:
        self._register(self.processors, name, factory)

    def register_exporter(self, name: str, factory: Factory) -> None:
        self._register(self.exporters, name, factory)

    def register_auth_provider(self, name: str, factory: Factory) -> None:
        self._register(self.auth_providers, name, factory)

    def register_parser(self, name: str, factory: Factory) -> None:
        self._register(self.parsers, name, factory)

    def register_extractor(self, name: str, factory: Factory) -> None:
        self._register(self.extractors, name, factory)

    def register_transformer(self, name: str, factory: Factory) -> None:
        self._register(self.transformers, name, factory)

    def register_hook(self, event: str, callback: Factory) -> None:
        key = event.strip().lower()
        if not key:
            raise ValueError("Hook event cannot be empty")
        if not callable(callback):
            raise TypeError(f"Hook must be callable: {event}")
        self.hooks.setdefault(key, []).append(callback)

    def register_theme(self, theme_id: str, label: str, *, tokens: dict[str, str]) -> None:
        """注册 UI 主题（覆盖 VisualTokens 色值令牌，见 gui.design_system）。"""
        theme_id = theme_id.strip().lower()
        if not theme_id or not label.strip():
            raise ValueError("主题 ID 与名称不能为空")
        if theme_id in self.themes:
            raise ValueError(f"主题重复: {theme_id}")
        if not isinstance(tokens, dict):
            raise TypeError("主题 tokens 必须是字典")
        self.themes[theme_id] = ThemeRegistration(theme_id, label, dict(tokens))

    def register_ui_action(
        self, action_id: str, label: str, callback: Callable[..., Any], *, section: str = "plugins"
    ) -> None:
        """注册菜单动作；回调可接受 (mw) 或 ()。section 用于菜单分组。"""
        action_id = action_id.strip().lower()
        if not action_id or not label.strip():
            raise ValueError("动作 ID 与名称不能为空")
        if not callable(callback):
            raise TypeError(f"动作回调必须是可调用对象: {action_id}")
        if action_id in self.ui_actions:
            raise ValueError(f"动作重复: {action_id}")
        self.ui_actions[action_id] = UIActionRegistration(action_id, label, callback, section)

    def register_ui_panel(self, panel_id: str, title: str, widget_factory: Callable[..., Any]) -> None:
        """注册侧栏面板；widget_factory(mw) 返回 QWidget。"""
        panel_id = panel_id.strip().lower()
        if not panel_id or not title.strip():
            raise ValueError("面板 ID 与名称不能为空")
        if not callable(widget_factory):
            raise TypeError(f"面板工厂必须是可调用对象: {panel_id}")
        if panel_id in self.ui_panels:
            raise ValueError(f"面板重复: {panel_id}")
        self.ui_panels[panel_id] = UIPanelRegistration(panel_id, title, widget_factory)

    def register_status_widget(self, widget_factory: Callable[..., Any]) -> None:
        """注册状态栏小部件；widget_factory() 返回 QWidget。"""
        if not callable(widget_factory):
            raise TypeError("状态小部件工厂必须是可调用对象")
        self.status_widgets.append(StatusWidgetRegistration(widget_factory))

    def emit(self, event: str, *, fail_open: bool = False, **context: Any) -> list[Any]:
        event_name = event.strip().lower()
        results: list[Any] = []
        for callback in self.hooks.get(event_name, []):
            try:
                results.append(callback(**context))
            except Exception as exc:
                if not fail_open:
                    raise
                with self._error_lock:
                    self.plugin_errors.append(
                        {
                            "path": f"hook:{event_name}",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                    )
        return results

    @staticmethod
    def _register(bucket: dict[str, Factory], name: str, factory: Factory) -> None:
        key = name.strip().lower()
        if not key:
            raise ValueError("插件名称不能为空")
        if key in bucket:
            raise ValueError(f"插件名称重复: {key}")
        bucket[key] = factory

    def describe(self) -> dict[str, Any]:
        return {
            "sources": sorted(self.sources),
            "fetchers": sorted(self.fetchers),
            "processors": sorted(self.processors),
            "exporters": sorted(self.exporters),
            "auth_providers": sorted(self.auth_providers),
            "parsers": sorted(self.parsers),
            "extractors": sorted(self.extractors),
            "transformers": sorted(self.transformers),
            "hooks": {name: len(callbacks) for name, callbacks in sorted(self.hooks.items())},
            "ui": {
                "themes": sorted(self.themes),
                "actions": sorted(self.ui_actions),
                "panels": sorted(self.ui_panels),
                "status_widgets": len(self.status_widgets),
            },
            "plugins": [f"{item.name}@{item.version}" for item in self.plugins],
            "plugin_details": [
                {
                    "name": item.name,
                    "version": item.version,
                    "types": list(item.plugin_types),
                    "capabilities": list(item.capabilities),
                    "domains": list(item.domains),
                    "license": item.license,
                    "fallback": item.fallback,
                    "execution_mode": "in_process_trusted",
                }
                for item in self.plugins
            ],
            "errors": list(self.plugin_errors),
        }


def _resolve_trust_root(config: AppConfig | None, trust_source: str) -> str:
    """Resolve a trust root given as inline PEM or a (root-relative) path."""

    if "-----BEGIN" in trust_source:
        return trust_source
    candidate = Path(trust_source)
    if not candidate.is_absolute() and config is not None:
        candidate = config.resolve(trust_source)
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8").strip()
    return trust_source


def _verify_plugin_signature(
    path: Path,
    config: AppConfig | None,
    *,
    signature_policy: str,
    trust_prompter: TrustPrompter | None,
) -> None:
    """签名验签门（按来源分级 + 签名策略）。

    分层：
    - 维护者签名（``maintainer.sig``/遗留 ``plugin.py.sig``）经信任根验证 → 自动信任；
    - 创作者签名（``creator.sig`` + ``creator.identity``）指纹在本地信任列表 → 信任；
    - 创作者签名有效但未在信任列表：strict 策略下经信任询问器确认
      （信任并加载 / 仅本次加载 / 拒绝）；developer 策略下警告放行；
    - 无有效签名：strict 一律拒载；developer 策略下警告放行（开发模式）。
    """

    trust_source = ""
    if config is not None and config.plugin_trust_public_key:
        trust_source = _resolve_trust_root(config, config.plugin_trust_public_key)
    if path.name != "plugin.py":
        # 单文件形态（开发期工具）：仅支持信任根对 <file>.sig 的验签（旧行为）。
        ok, reason = signing.verify_plugin(path, trust_source)
        if not ok:
            if signature_policy == SIGNATURE_POLICY_DEVELOPER:
                LOGGER.warning(
                    "开发者模式：未验签加载本地插件 %s（%s）",
                    path,
                    reason,
                )
                return
            raise signing.PluginSignatureError(f"插件签名校验失败，拒绝加载: {path}（{reason}）")
        return
    from . import trust as trust_model

    is_market = MARKET_DIR_NAME in path.parent.parts
    decision = trust_model.verify_plugin_trust(
        path.parent, trust_source, trust_model.TrustedUserList()
    )
    level = decision.level
    if is_market:
        # 市场来源：仅接受维护者签名（内置信任根验签），创作者签名不足以放行
        if level == trust_model.TrustLevel.MaintainerSigned:
            return
        if signature_policy == SIGNATURE_POLICY_DEVELOPER:
            LOGGER.warning(
                "开发者模式：市场目录插件未经信任根验签，警告放行: %s（%s）",
                path,
                decision.reason,
            )
            return
        raise signing.PluginSignatureError(
            f"市场插件必须通过信任根验签，拒绝加载: {path}（{decision.reason}）"
        )
    if level == trust_model.TrustLevel.MaintainerSigned:
        return
    if level == trust_model.TrustLevel.CreatorTrusted:
        LOGGER.info("创作者信任列表命中，加载插件: %s", path)
        return
    if level == trust_model.TrustLevel.CreatorUntrusted and decision.creator is not None:
        creator = decision.creator
        if signature_policy == SIGNATURE_POLICY_DEVELOPER:
            LOGGER.warning(
                "开发者模式：加载未信任创作者插件 %s（作者 %s，指纹 %s）",
                path,
                creator.username,
                creator.key_fingerprint,
            )
            return
        if trust_prompter is not None:
            result = trust_prompter(
                path.parent.name, creator.username, creator.key_fingerprint
            )
            if result == TrustPromptResult.TRUST_AND_LOAD:
                trust_model.TrustedUserList().add(
                    creator, source="local", path_hint=f"（{path}）"
                )
                LOGGER.info(
                    "已信任作者 %s（指纹 %s）并加载插件: %s",
                    creator.username,
                    creator.key_fingerprint,
                    path,
                )
                return
            if result == TrustPromptResult.LOAD_ONCE:
                LOGGER.info("仅本次加载（作者未入信任列表）: %s", path)
                return
        raise signing.PluginSignatureError(
            f"插件 {path} 拒绝加载：作者 {creator.username}（指纹 {creator.key_fingerprint}）"
            " 未在本地信任列表。信任命令: python tools/identity.py trust add "
            f"{creator.key_fingerprint} --name {creator.username}"
        )
    if signature_policy == SIGNATURE_POLICY_DEVELOPER:
        LOGGER.warning(
            "开发者模式：未验签加载本地插件 %s（%s）；"
            "生产环境应使用 strict 策略并配置信任根",
            path,
            decision.reason,
        )
        return
    raise signing.PluginSignatureError(f"插件签名校验失败，拒绝加载: {path}（{decision.reason}）")


def load_local_plugins(
    registry: Registry,
    paths: list[str],
    root: Path,
    *,
    allow_external_paths: bool = False,
    fail_open: bool = False,
    approved_permissions: tuple[str, ...] = (),
    ast_allowed_patterns: tuple[str, ...] = (),
    signature_policy: str = SIGNATURE_POLICY_DEVELOPER,
    trust_prompter: TrustPrompter | None = None,
    config: AppConfig | None = None,
    egress: EgressBroker | None = None,
) -> None:
    """加载本地插件。

    ``signature_policy`` 默认 developer（与 config=None 的程序化调用兼容，
    仅测试/开发用途）；产品入口 ``build_registry`` 按配置传入 strict。
    """
    root = root.resolve()
    expanded: list[str] = []
    for value in paths:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = (root / value).resolve()
        if not candidate.exists():
            # 配置的插件路径不存在：记为非致命提示并跳过，不抛异常。
            # 这样零配置默认路径（如尚未创建的 plugins_installed/）不会使加载崩溃，
            # 同时用户手滑写错的路径仍有可见记录（fail_open 语义对此类情况同样适用）。
            registry.plugin_errors.append(
                {"path": str(value), "error": "skipped: 路径不存在", "level": "skipped"}
            )
            LOGGER.debug("跳过不存在的插件路径: %s", value)
            continue
        if candidate.is_dir():
            # 目录模式：递归加载目录下所有插件（如 plugins_installed）。
            # 仅取 .py，排除 __pycache__ 与 .sig。
            for py in sorted(candidate.rglob("*.py")):
                if "__pycache__" in py.parts:
                    continue
                expanded.append(str(py))
            continue
        expanded.append(value)
    for index, value in enumerate(expanded):
        try:
            _load_local_plugin(
                registry,
                value,
                root,
                index,
                allow_external_paths,
                approved_permissions,
                ast_allowed_patterns,
                signature_policy,
                trust_prompter,
                config,
                egress,
            )
        except Exception as exc:
            if not fail_open:
                raise
            registry.plugin_errors.append({"path": str(value), "error": f"{type(exc).__name__}: {exc}"})


def _load_local_plugin(
    registry: Registry,
    value: str,
    root: Path,
    index: int,
    allow_external_paths: bool,
    approved_permissions: tuple[str, ...],
    ast_allowed_patterns: tuple[str, ...],
    signature_policy: str,
    trust_prompter: TrustPrompter | None,
    config: AppConfig | None,
    egress: EgressBroker | None,
) -> None:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (root / path).resolve()
    if not path.is_file() or path.suffix != ".py":
        raise FileNotFoundError(f"插件文件不存在或不是.py文件: {path}")
    path = path.resolve()
    if not allow_external_paths and root not in path.parents:
        raise PermissionError(f"默认禁止加载项目目录之外的插件: {path}")
    requested = _preflight_permissions(path)
    approved = {item.casefold() for item in approved_permissions}
    if MARKET_DIR_NAME not in path.parent.parts:
        # 本地来源插件：ui:* 权限族自动放行（GUI 插件宿主按注册类型挂载）
        approved |= UI_PERMISSIONS
    denied = requested - approved
    if denied:
        raise PermissionError(
            f"Plugin permissions were not approved: {', '.join(sorted(denied))}; file={path}"
        )
    network_imports, dangerous_patterns = _preflight_forbidden_patterns(
        path, allowed=set(ast_allowed_patterns)
    )
    if network_imports:
        raise PermissionError(
            "插件不得直接导入网络客户端；请声明network权限、domains，并使用"
            f"PluginContext.network: {', '.join(sorted(network_imports))}"
        )
    if dangerous_patterns:
        raise PermissionError(
            "插件包含禁止的危险调用/导入: "
            f"{', '.join(sorted(dangerous_patterns))}；如确属需要，请在插件文件头添加注释 "
            "'# omnicrawl: allow-ast <名称>' 或在配置 plugins.ast_allowed_patterns 中声明豁免"
        )
    _verify_plugin_signature(
        path,
        config,
        signature_policy=signature_policy,
        trust_prompter=trust_prompter,
    )
    LOGGER.warning(
        "Loading trusted local plugin in the main process: %s. "
        "Do not use plugins.paths for untrusted code; signed subprocess plugins are the target migration path.",
        path,
    )
    name = f"omnicrawl_user_plugin_{index}_{path.stem}"
    mtime_key = (path, path.stat().st_mtime_ns)
    with _PLUGIN_CACHE_LOCK:
        cached = _PLUGIN_MODULE_CACHE.get(mtime_key)
        if cached is not None:
            # S2.5.41：缓存命中——复用已执行模块，跳过重复编译
            module = cached
        else:
            spec = importlib.util.spec_from_file_location(name, path)
            if not spec or not spec.loader:
                raise ImportError(f"无法加载插件: {path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _PLUGIN_MODULE_CACHE[mtime_key] = module
    register = getattr(module, "register", None)
    if not callable(register):
        raise TypeError(f"插件必须提供register(registry)函数: {path}")
    metadata = _metadata(module, path)
    # S1.3.7：运行时权限必须是静态字面量审批集的子集——动态计算/拼接的
    # metadata 无法绕过权限门（其静态预检结果为空，任何运行时权限即越界）。
    live_permissions = {item.casefold() for item in metadata.permissions}
    if live_permissions - requested:
        raise PermissionError(
            "插件声明了静态审批之外的权限（PLUGIN_METADATA 必须为字面量，"
            f"不支持动态计算）: {', '.join(sorted(live_permissions - requested))}; file={path}"
        )
    network = None
    capability = None
    if "network" in {item.casefold() for item in metadata.permissions}:
        if config is None or egress is None:
            raise RuntimeError("网络插件只能由带Egress Broker的运行时加载")
        if not metadata.domains:
            raise ValueError("请求network权限的插件必须声明domains")
        from .plugin_runtime import PluginNetworkClient

        maximum = int(metadata.resource_limits.get("maximum_requests", 0))
        capability = egress.issue_capability(
            metadata.name,
            domains=metadata.domains,
            purposes=("plugin",),
            maximum_requests=maximum,
        )
        network = PluginNetworkClient(config, egress, capability)
    context = PluginContext(metadata, network)
    try:
        try:
            signature = inspect.signature(register)
        except (TypeError, ValueError):
            register(registry)
        else:
            if _signature_accepts(signature, registry, context):
                register(registry, context)
            elif _signature_accepts(signature, registry):
                register(registry)
            else:
                raise TypeError("插件register必须接受(registry)或(registry, context)")
    except Exception:
        # A failed registration must not leave a usable network capability
        # behind for an object captured during partial module setup.
        if capability is not None and egress is not None:
            egress.revoke_capability(capability)
        raise
    registry.plugins.append(metadata)


def _signature_accepts(signature: inspect.Signature, *arguments: Any) -> bool:
    try:
        signature.bind(*arguments)
    except TypeError:
        return False
    return True


_NETWORK_MODULES = {
    "socket",
    "requests",
    "httpx",
    "aiohttp",
    "websockets",
    "urllib.request",
    "http.client",
}

# 危险模块导入：直接导入即拒绝（加载前静态检查，不执行插件代码）
_FORBIDDEN_MODULES = {"subprocess", "ctypes", "winreg", "builtins"}

# 危险属性调用：module.attr 形态（含 from module import attr）
_FORBIDDEN_ATTR_CALLS = {
    ("os", "system"),
    ("os", "startfile"),
    ("os", "remove"),
    ("os", "unlink"),
    ("os", "rmdir"),
    ("os", "removedirs"),
    ("os", "kill"),
    ("os", "popen"),
    ("os", "execl"),
    ("os", "execle"),
    ("os", "execv"),
    ("os", "execve"),
    ("os", "execvp"),
    ("os", "execvpe"),
    ("os", "spawnl"),
    ("os", "spawnle"),
    ("os", "spawnlp"),
    ("os", "spawnlpe"),
    ("os", "spawnv"),
    ("os", "spawnve"),
    ("os", "spawnvp"),
    ("os", "spawnvpe"),
    ("os", "posix_spawn"),
    ("shutil", "rmtree"),
    ("importlib", "import_module"),
    ("importlib.util", "spec_from_file_location"),
    ("importlib.util", "spec_from_loader"),
}

# 危险内建调用（eval/exec 动态执行）
_FORBIDDEN_BUILTIN_CALLS = {"eval", "exec"}

# 文件内豁免注释：# omnicrawl: allow-ast <pattern-id>（如 os.system、subprocess、eval）
_ALLOW_AST_COMMENT_RE = re.compile(r"^\s*#\s*omnicrawl:\s*allow-ast\s+([\w.]+)\s*$", re.IGNORECASE)


def _preflight_forbidden_patterns(path: Path, allowed: set[str]) -> tuple[set[str], set[str]]:
    """AST 静态检查插件源码，返回 (网络导入, 其他危险模式)。

    两类均为空才允许加载。``allowed`` 提供豁免的 pattern id（模块名、
    调用名如 ``os.system``），来源：``plugins.ast_allowed_patterns`` 配置
    或文件内 ``# omnicrawl: allow-ast <id>`` 注释。豁免必须显式、可审计。
    """
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return set(), set()
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return set(), set()
    allowed = set(allowed)
    for line in source.splitlines():
        match = _ALLOW_AST_COMMENT_RE.search(line)
        if match:
            allowed.add(match.group(1))

    network: set[str] = set()
    dangerous: set[str] = set()
    alias: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                top = item.name.split(".")[0]
                alias[item.asname or top] = top
                if top in _FORBIDDEN_MODULES and top not in allowed:
                    dangerous.add(top)
                if top not in allowed and any(
                    item.name == m or item.name.startswith(m + ".") for m in _NETWORK_MODULES
                ):
                    network.add(item.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            top = module.split(".")[0]
            if top in _FORBIDDEN_MODULES and top not in allowed:
                dangerous.add(top)
            if top not in allowed and any(
                module == m or module.startswith(m + ".") for m in _NETWORK_MODULES
            ):
                network.add(module)
            for item in node.names:
                if item.name == "*":
                    continue
                if top in _FORBIDDEN_MODULES and top not in allowed:
                    dangerous.add(top)
                pair = f"{top}.{item.name}"
                if (top, item.name) in _FORBIDDEN_ATTR_CALLS and pair not in allowed:
                    dangerous.add(pair)
                alias[item.asname or item.name] = pair
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id in _FORBIDDEN_BUILTIN_CALLS and func.id not in allowed:
                    dangerous.add(func.id)
                if func.id == "__import__":
                    if (
                        node.args
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)
                    ):
                        name = node.args[0].value
                        if (
                            any(name == m or name.startswith(m + ".") for m in _NETWORK_MODULES)
                            and name not in allowed
                        ):
                            network.add(name)
            elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
                module = alias.get(func.value.id, func.value.id)
                pair = f"{module}.{func.attr}"
                if (module, func.attr) in _FORBIDDEN_ATTR_CALLS and pair not in allowed:
                    dangerous.add(pair)
    return network, dangerous


def _preflight_permissions(path: Path) -> set[str]:
    """Read literal metadata from the AST before importing and executing plugin code."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError):
        return set()
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "PLUGIN_METADATA" for target in targets):
            continue
        if node.value is None:
            return set()
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            return set()
        if isinstance(value, dict):
            permissions = value.get("permissions", [])
            if isinstance(permissions, (list, tuple)):
                return {str(item).casefold() for item in permissions}
        return set()
    return set()
