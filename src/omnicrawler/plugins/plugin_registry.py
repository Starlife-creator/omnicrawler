"""插件注册表：按官方扩展点分类的工厂/回调容器，以及注册与查询。

纯容器，不感知加载流程（加载见 plugin_loader.py）；契约类型来自 plugin_contracts.py。
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from .plugin_contracts import (
    BackgroundRegistration,
    PluginMetadata,
    StatusWidgetRegistration,
    ThemeRegistration,
    UIActionRegistration,
    UIPanelRegistration,
)

Factory = Callable[..., Any]
LOGGER = logging.getLogger(__name__)

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
        self.resource_providers: dict[str, Any] = {}
        self.declarative_views: dict[str, Any] = {}
        self.hooks: dict[str, list[Factory]] = {}
        self.themes: dict[str, ThemeRegistration] = {}
        self.ui_actions: dict[str, UIActionRegistration] = {}
        self.ui_panels: dict[str, UIPanelRegistration] = {}
        self.status_widgets: list[StatusWidgetRegistration] = []
        self.backgrounds: dict[str, BackgroundRegistration] = {}
        self.plugins: list[PluginMetadata] = []
        self.plugin_errors: list[dict[str, str]] = []
        self._resources: list[Any] = []
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

    def register_background(
        self,
        background_id: str,
        label: str,
        *,
        default_opacity: float = 0.24,
        default_dim: float = 0.30,
    ) -> None:
        """注册由宿主渲染的本地媒体背景，不接受 QWidget 或绘制回调。"""

        normalized = background_id.strip().casefold()
        if not normalized or not label.strip():
            raise ValueError("背景 ID 与名称不能为空")
        if normalized in self.backgrounds:
            raise ValueError(f"背景重复: {normalized}")
        opacity = float(default_opacity)
        dim = float(default_dim)
        if not 0.05 <= opacity <= 0.85 or not 0.0 <= dim <= 0.85:
            raise ValueError("背景默认透明度或遮罩强度超出宿主安全范围")
        self.backgrounds[normalized] = BackgroundRegistration(
            normalized,
            label.strip(),
            opacity,
            dim,
        )

    def emit(self, event: str, *, fail_open: bool = False, **context: Any) -> list[Any]:
        # B01-009：fail_open 指「事件回调容错」（回调抛错时吞掉继续，不致命），
        # 与网络/信任的 fail-open（不安全）无关；本方法不涉及安全判定。
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

    def track_resource(self, resource: Any) -> None:
        if not any(existing is resource for existing in self._resources):
            self._resources.append(resource)

    def bind_plugin_runtime(self, *, config: Any, state_store: Any) -> None:
        """Attach host-owned runtime services to isolated plugin resources."""

        for resource in self._resources:
            bind = getattr(resource, "bind_runtime", None)
            if callable(bind):
                bind(config=config, state_store=state_store)

    def bind_plugin_run(self, run_id: str) -> None:
        """Update the current run namespace before any adapter invocation."""

        for resource in self._resources:
            bind = getattr(resource, "bind_run", None)
            if callable(bind):
                bind(run_id)

    def close(self) -> None:
        """关闭由契约 2 adapter 共享的子进程资源。"""
        errors: list[Exception] = []
        for resource in reversed(self._resources):
            close = getattr(resource, "close", None)
            if not callable(close):
                continue
            try:
                close()
            except Exception as exc:  # noqa: BLE001 - 逐资源隔离关闭
                errors.append(exc)
        self._resources.clear()
        if errors:
            raise RuntimeError("插件资源关闭失败: " + "; ".join(str(item) for item in errors))

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
            "resource_providers": sorted(self.resource_providers),
            "declarative_views": sorted(self.declarative_views),
            "hooks": {name: len(callbacks) for name, callbacks in sorted(self.hooks.items())},
            "ui": {
                "themes": sorted(self.themes),
                "actions": sorted(self.ui_actions),
                "panels": sorted(self.ui_panels),
                "status_widgets": len(self.status_widgets),
                "backgrounds": sorted(self.backgrounds),
            },
            "plugins": [f"{item.name}@{item.version}" for item in self.plugins],
            "plugin_details": [
                {
                    "name": item.name,
                    "version": item.version,
                    "types": list(item.plugin_types),
                    "category": item.category,
                    "tags": list(item.tags),
                    "capabilities": list(item.capabilities),
                    "domains": list(item.domains),
                    "license": item.license,
                    "fallback": item.fallback,
                    # Phase 1（基线修复）：动态输出真实声明模式，不再硬编码
                    # in_process_trusted（0.10 起运行期实际后端由路由矩阵裁决，
                    # Phase 2 接线 B4 后此处输出运行态模式）
                    "execution_mode": item.execution_mode,
                    # Phase 3（B2）：契约形态列（2=契约 2 handle / 1=契约 1 register）
                    "contract_shape": item.contract_shape,
                }
                for item in self.plugins
            ],
            "errors": list(self.plugin_errors),
        }
