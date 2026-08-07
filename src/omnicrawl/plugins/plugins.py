from __future__ import annotations

import ast
import importlib.util
import inspect
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
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
        raise RuntimeError(
            f"插件API版本不兼容: {path} 需要{result.api_version}，当前为{PLUGIN_API_VERSION}"
        )
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
                    self.plugin_errors.append({
                        "path": f"hook:{event_name}",
                        "error": f"{type(exc).__name__}: {exc}",
                    })
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
            "sources": sorted(self.sources), "fetchers": sorted(self.fetchers),
            "processors": sorted(self.processors), "exporters": sorted(self.exporters),
            "auth_providers": sorted(self.auth_providers), "parsers": sorted(self.parsers),
            "extractors": sorted(self.extractors),
            "transformers": sorted(self.transformers),
            "hooks": {name: len(callbacks) for name, callbacks in sorted(self.hooks.items())},
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


def _verify_plugin_signature(path: Path, config: AppConfig | None) -> None:
    """Fail-closed signature gate, run before a plugin module is executed.

    - Trust root configured: verify the detached ``.sig``; any failure raises
      ``PluginSignatureError`` (the plugin is rejected, fail-closed).
    - No trust root configured: warn explicitly (never silently accept) and
      allow loading (transition period for existing dev plugins).
    """

    trust_source = ""
    if config is not None and config.plugin_trust_public_key:
        trust_source = _resolve_trust_root(config, config.plugin_trust_public_key)
    if not trust_source:
        LOGGER.warning(
            "插件签名信任根未配置，将以'未验签'方式加载本地插件 %s；"
            "生产环境应在 plugins.trust_public_key 配置 ed25519 公钥以启用 fail-closed 验签。",
            path,
        )
        return
    ok, reason = signing.verify_plugin(path, trust_source)
    if not ok:
        raise signing.PluginSignatureError(f"插件签名校验失败，拒绝加载: {path}（{reason}）")


def load_local_plugins(
    registry: Registry,
    paths: list[str],
    root: Path,
    *,
    allow_external_paths: bool = False,
    fail_open: bool = False,
    approved_permissions: tuple[str, ...] = (),
    config: AppConfig | None = None,
    egress: EgressBroker | None = None,
) -> None:
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
        denied = requested - {item.casefold() for item in approved_permissions}
        if denied:
            raise PermissionError(
                f"Plugin permissions were not approved: {', '.join(sorted(denied))}; file={path}"
            )
        forbidden_imports = _preflight_network_imports(path)
        if forbidden_imports:
            raise PermissionError(
                "插件不得直接导入网络客户端；请声明network权限、domains，并使用"
                f"PluginContext.network: {', '.join(sorted(forbidden_imports))}"
            )
        _verify_plugin_signature(path, config)
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


def _preflight_network_imports(path: Path) -> set[str]:
    """Reject direct transports before executing plugin module-level code."""

    network_modules = {
        "socket", "requests", "httpx", "aiohttp", "websockets", "urllib.request", "http.client"
    }
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, UnicodeError, SyntaxError):
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            if any(name == module or name.startswith(module + ".") for module in network_modules):
                found.add(name)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "__import__":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                name = node.args[0].value
                if any(name == module or name.startswith(module + ".") for module in network_modules):
                    found.add(name)
    return found


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
