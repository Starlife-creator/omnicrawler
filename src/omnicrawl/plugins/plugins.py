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

Factory = Callable[..., Any]
PLUGIN_API_VERSION = 1
CORE_VERSION = __version__
LOGGER = logging.getLogger(__name__)


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
    for index, value in enumerate(paths):
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
        LOGGER.warning(
            "Loading trusted local plugin in the main process: %s. "
            "Do not use plugins.paths for untrusted code; signed subprocess plugins are the target migration path.",
            path,
        )
        name = f"omnicrawl_user_plugin_{index}_{path.stem}"
        spec = importlib.util.spec_from_file_location(name, path)
        if not spec or not spec.loader:
            raise ImportError(f"无法加载插件: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        register = getattr(module, "register", None)
        if not callable(register):
            raise TypeError(f"插件必须提供register(registry)函数: {path}")
        metadata = _metadata(module, path)
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
