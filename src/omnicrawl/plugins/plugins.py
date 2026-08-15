from __future__ import annotations

import ast
import importlib.util
import inspect
import io
import logging
import threading
import tokenize
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

# 市场安装目录名：位于**项目根下该目录**的插件视为市场来源
# （维护者签名+信任根门禁）。市场来源判定必须是「规范的安装位置」，
# 而不是"路径里碰巧出现这个名字"——后者可被任意目录名伪造
# （审查报告 B10：把市场插件挪进 plugins/ 即逃脱维护者签名要求）。
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
    plugin_bytes: bytes | None = None,
    is_market: bool = False,
) -> Any:
    """签名验签门（按来源分级 + 签名策略）。

    分层：
    - 维护者签名（``plugin.py.sig``；旧版 ``maintainer.sig`` 已弃用，验证器不再兼容）经信任根验证 → 自动信任；
    - 创作者签名（``creator.sig`` + ``creator.identity``）指纹在本地信任列表 → 信任；
    - 创作者签名有效但未在信任列表：strict 策略下经信任询问器确认
      （信任并加载 / 仅本次加载 / 拒绝）；developer 策略下警告放行；
    - 无有效签名：strict 一律拒载；developer 策略下警告放行（开发模式）。

    ``plugin_bytes`` 传入调用方已读好的内容，验签基于**同一份字节**（TOCTOU 消除，
    审查报告 S49）。返回 TrustDecision（含通过验签的 verified_bytes），供调用方
    原样执行；未通过时抛 PluginSignatureError。
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
                return None
            raise signing.PluginSignatureError(f"插件签名校验失败，拒绝加载: {path}（{reason}）")
        return None
    from . import trust as trust_model

    decision = trust_model.verify_plugin_trust(
        path.parent,
        trust_source,
        trust_model.TrustedUserList(),
        plugin_bytes=plugin_bytes,
    )
    level = decision.level
    if is_market:
        # 市场来源：仅接受维护者签名（内置信任根验签），创作者签名不足以放行。
        # 信任根缺失时（trust_root_available=False）维护者层级根本没被评估，
        # 一律拒绝——绝不把"没查到"当成"通过"（审查报告 S51）。
        if level == trust_model.TrustLevel.MaintainerSigned:
            return decision
        if signature_policy == SIGNATURE_POLICY_DEVELOPER:
            LOGGER.warning(
                "开发者模式：市场目录插件未经信任根验签，警告放行: %s（%s）",
                path,
                decision.reason,
            )
            return decision
        raise signing.PluginSignatureError(
            f"市场插件必须通过信任根验签，拒绝加载: {path}（{decision.reason}）"
        )
    if level == trust_model.TrustLevel.MaintainerSigned:
        return decision
    if level == trust_model.TrustLevel.CreatorTrusted:
        LOGGER.info("创作者信任列表命中，加载插件: %s", path)
        return decision
    if level == trust_model.TrustLevel.CreatorUntrusted and decision.creator is not None:
        creator = decision.creator
        if signature_policy == SIGNATURE_POLICY_DEVELOPER:
            LOGGER.warning(
                "开发者模式：加载未信任创作者插件 %s（作者 %s，指纹 %s）",
                path,
                creator.username,
                creator.key_fingerprint,
            )
            return decision
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
                return decision
            if result == TrustPromptResult.LOAD_ONCE:
                LOGGER.info("仅本次加载（作者未入信任列表）: %s", path)
                return decision
        raise signing.PluginSignatureError(
            f"插件 {path} 拒绝加载：作者 {creator.username}（指纹 {creator.key_fingerprint}）"
            " 未在本地信任列表。信任命令: python tools/identity.py trust add "
            f"--pubkey <作者的 .pem 公钥文件> --name {creator.username}"
        )
    if signature_policy == SIGNATURE_POLICY_DEVELOPER:
        LOGGER.warning(
            "开发者模式：未验签加载本地插件 %s（%s）；"
            "生产环境应使用 strict 策略并配置信任根",
            path,
            decision.reason,
        )
        return decision
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
    signature_policy: str = SIGNATURE_POLICY_STRICT,
    trust_prompter: TrustPrompter | None = None,
    config: AppConfig | None = None,
    egress: EgressBroker | None = None,
) -> None:
    """加载本地插件。

    ``signature_policy`` 默认 strict（B01-004：安全门默认最严，放松必须显式声明）；
    测试/开发用途需放宽时显式传 developer。
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
            # 目录模式：递归加载目录下插件（如 plugins_installed）。
            # B01-005（D9）：只认规范布局入口 plugin.py；辅助模块（helpers.py 等）交给
            # 插件自己 import，游离 .py（conftest.py/test_*.py/编辑器临时文件）不当作插件，
            # 记为 error 跳过——避免单文件残留打挂整批加载。
            for py in sorted(candidate.rglob("plugin.py")):
                if "__pycache__" in py.parts:
                    continue
                expanded.append(str(py))
            stray = [
                str(path) for path in sorted(candidate.rglob("*.py"))
                if path.name != "plugin.py" and "__pycache__" not in path.parts
            ]
            if stray:
                LOGGER.warning(
                    "目录模式忽略非入口 .py 文件（辅助模块请由插件自行 import，游离文件请移出插件目录）: %s",
                    ", ".join(stray[:10]),
                )
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

    # ── 全链路共用同一份字节：预检 / 验签 / 执行全部基于本次读取（S49）。
    #    此后不再对磁盘做第二次读取，杜绝"验签读 A、执行读 B"的 TOCTOU 窗口。
    plugin_bytes = path.read_bytes()
    source = _decode_plugin_source(path, plugin_bytes)

    # 市场来源判定：规范安装位置 = <项目根>/<市场目录名>/<插件id>/plugin.py。
    # 仅当父目录**精确等于**该位置时才视为市场插件；路径里任何别处出现
    # "plugins_installed" 字样都不算（审查报告 B10）。
    is_market = path.parent == (root / MARKET_DIR_NAME / path.parent.name)

    requested = _preflight_permissions(path, source)
    approved = {item.casefold() for item in approved_permissions}
    if not is_market:
        # 本地来源插件：ui:* 权限族自动放行（GUI 插件宿主按注册类型挂载）
        approved |= UI_PERMISSIONS
    denied = requested - approved
    if denied:
        raise PermissionError(
            f"Plugin permissions were not approved: {', '.join(sorted(denied))}; file={path}"
        )
    network_imports, dangerous_patterns = _preflight_forbidden_patterns(
        path, source, allowed=set(ast_allowed_patterns)
    )
    if network_imports:
        raise PermissionError(
            "插件不得直接导入网络客户端；请声明network权限、domains，并使用"
            f"PluginContext.network: {', '.join(sorted(network_imports))}"
        )
    if dangerous_patterns:
        raise PermissionError(
            "插件包含禁止的危险调用/导入: "
            f"{', '.join(sorted(dangerous_patterns))}；如确属需要，请在配置 "
            "plugins.ast_allowed_patterns 中显式声明豁免"
        )
    decision = _verify_plugin_signature(
        path,
        config,
        signature_policy=signature_policy,
        trust_prompter=trust_prompter,
        plugin_bytes=plugin_bytes,
        is_market=is_market,
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
            # 执行**通过验签的那一份字节**（decision.verified_bytes），而非重新读盘。
            # developer 策略下验签可能未执行（decision 为 None 或 verified_bytes 为空），
            # 此时回退到本次已读的 plugin_bytes——同样是单次读取的内容。
            exec_bytes = (
                decision.verified_bytes if decision is not None and decision.verified_bytes else plugin_bytes
            )
            code = compile(_decode_plugin_source(path, exec_bytes), str(path), "exec")
            exec(code, module.__dict__)
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
        # S52：域名与额度此前完全由插件自声明——"批准 network" 等于
        # "批准任意公网外传"。这里做两道机械收紧：
        # ① 域名必须是"可解析主机"形态：含至少一个点且非纯后缀（拒绝
        #    domains=["com"] 这类访问任意 *.com 的宽泛声明）；
        # ② maximum_requests 取「插件声明」与「配置 egress.maximum_requests
        #    上限」的较小值（配置 0 表示无上限，此时仍以插件声明为准）。
        for domain in metadata.domains:
            normalized = str(domain).strip().rstrip(".")
            if (
                "." not in normalized
                or normalized.endswith((".", ".."))
                or not all(part and part.isalnum() or part == "-" for part in normalized.split("."))
            ):
                raise PermissionError(
                    f"插件 network 域名为宽泛或非法形态，拒绝加载: {domain!r}"
                    "（域名须为可解析主机名，如 api.example.com）"
                )
        from .plugin_runtime import PluginNetworkClient

        declared = int(metadata.resource_limits.get("maximum_requests", 0))
        configured = 0
        if config is not None:
            egress_section = config.section("egress") or {}
            try:
                configured = int(egress_section.get("maximum_requests", 0) or 0)
            except (TypeError, ValueError):
                configured = 0
        if declared > 0 and configured > 0:
            maximum = min(declared, configured)
        else:
            maximum = declared or configured
        capability = egress.issue_capability(
            metadata.name,
            domains=tuple(metadata.domains),
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
# 动态导入函数：`__import__` / `importlib.import_module` 是绕过 AST 门的
# 常规入口（`__import__('os').system('id')` 的 func.value 是 Call 而非 Name，
# 旧实现因此漏判——审查报告 B2）。任何出现都直接判危险，杜绝"借道导入"。
_FORBIDDEN_IMPORT_FUNCS = {"__import__", "import_module"}


def _decode_plugin_source(path: Path, data: bytes) -> str:
    """按 PEP 263 编码声明解码插件源码。

    尊重文件头 ``# -*- coding: xxx -*-``（tokenize.detect_encoding 负责），
    解码失败即抛 PermissionError——**绝不**返回空内容蒙混过关
    （审查报告 B2：旧实现用 utf-8 硬解，latin-1 文件抛 UnicodeDecodeError
    后被吞掉、预检返回空集 = fail-open）。
    """
    try:
        encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    except (SyntaxError, LookupError) as exc:
        raise PermissionError(f"插件源码编码声明非法，拒绝加载: {path}（{exc}）") from exc
    try:
        return data.decode(encoding)
    except UnicodeDecodeError as exc:
        raise PermissionError(
            f"插件源码无法按声明的编码解码（{encoding}），拒绝加载: {path}（{exc}）"
        ) from exc


def _preflight_forbidden_patterns(path: Path, source: str, allowed: set[str]) -> tuple[set[str], set[str]]:
    """AST 静态检查插件源码，返回 (网络导入, 其他危险模式)。

    两类均为空才允许加载。``allowed`` 提供豁免的 pattern id（模块名、
    调用名如 ``os.system``），**唯一**来源：``plugins.ast_allowed_patterns``
    配置——由管理员（运行配置）控制，不由插件自己声明。

    任何解析失败都抛 PermissionError（fail-closed）；语法错误的文件本来
    也无法执行，此处显式拒绝而非静默放行。
    """
    allowed = set(allowed)
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise PermissionError(f"插件源码解析失败，拒绝加载: {path}（{exc}）") from exc

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
                if item.name in _FORBIDDEN_IMPORT_FUNCS:
                    dangerous.add(f"{top}.{item.name}")
                alias[item.asname or item.name] = pair
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id in _FORBIDDEN_BUILTIN_CALLS and func.id not in allowed:
                    dangerous.add(func.id)
                if func.id == "__import__":
                    # __import__ 本身即动态导入入口：无论参数是否字面量，
                    # 一律拒绝（旧实现只在参数为常量字符串时检查网络模块，
                    # 其余情况漏过）。
                    dangerous.add("__import__")
            elif isinstance(func, ast.Attribute):
                if func.attr in _FORBIDDEN_IMPORT_FUNCS:
                    dangerous.add(f"<call>.{func.attr}")
                # 常规形态：<模块>.<属性>(...) —— 属性链逐层向上解析模块名
                resolved_module = _resolve_module_of_attribute(func, alias)
                if resolved_module is not None:
                    pair = f"{resolved_module}.{func.attr}"
                    if (resolved_module, func.attr) in _FORBIDDEN_ATTR_CALLS and pair not in allowed:
                        dangerous.add(pair)
    return network, dangerous


def _resolve_module_of_attribute(node: ast.Attribute, alias: dict[str, str]) -> str | None:
    """从属性调用链解析「模块.属性」中的模块名。

    覆盖三种形态：
    - ``os.system(...)``         → func.value 是 Name → "os"
    - ``alias.system(...)``      → func.value 是 Name，经 import as 别名映射
    - ``__import__('os').system(...)`` → func.value 是 Call —— 旧实现漏判
      的关键形态（审查报告 B2）：此处把 `__import__('<字面量>')` 的参数字面量
      当作模块名返回，命中 _FORBIDDEN_ATTR_CALLS 即拒绝。
    解析不出明确模块名时返回 None（不误报，交由其它规则兜底）。
    """
    value = node.value
    if isinstance(value, ast.Name):
        return alias.get(value.id, value.id)
    if isinstance(value, ast.Call):
        inner = value.func
        if isinstance(inner, ast.Name) and inner.id == "__import__":
            if (
                value.args
                and isinstance(value.args[0], ast.Constant)
                and isinstance(value.args[0].value, str)
            ):
                return value.args[0].value.split(".")[0]
        if (
            isinstance(inner, ast.Attribute)
            and inner.attr in _FORBIDDEN_IMPORT_FUNCS
            and value.args
            and isinstance(value.args[0], ast.Constant)
            and isinstance(value.args[0].value, str)
        ):
            return value.args[0].value.split(".")[0]
    return None


def _preflight_permissions(path: Path, source: str) -> set[str]:
    """Read literal metadata from the AST before importing and executing plugin code.

    fail-closed：解析/求值失败一律抛 PermissionError，绝不返回空集放行。
    """
    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        raise PermissionError(f"插件源码解析失败，拒绝加载: {path}（{exc}）") from exc
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        if not any(isinstance(target, ast.Name) and target.id == "PLUGIN_METADATA" for target in targets):
            continue
        if node.value is None:
            raise PermissionError(f"PLUGIN_METADATA 不能为空: {path}")
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError) as exc:
            raise PermissionError(f"PLUGIN_METADATA 必须是静态字面量: {path}（{exc}）") from exc
        if isinstance(value, dict):
            permissions = value.get("permissions", [])
            if isinstance(permissions, (list, tuple)):
                return {str(item).casefold() for item in permissions}
        raise PermissionError(f"PLUGIN_METADATA 结构非法: {path}")
    return set()
