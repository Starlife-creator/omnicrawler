"""Builder mixin: auth-provider, transformer and processor construction."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..plugins.plugin_runtime import build_extension
from ._mixin_base import _PipelineBase

if TYPE_CHECKING:
    from ..core.config import AppConfig


class _PipelineBuilders(_PipelineBase):
    def _build_auth_provider(self) -> Any | None:
        auth = self.config.section("auth")
        name = str(auth.get("provider", "")).strip().casefold()
        if not name:
            return None
        if name not in self.registry.auth_providers:
            raise KeyError(f"Unknown auth provider plugin: {name}")
        options = auth.get("options", {})
        if not isinstance(options, dict):
            raise TypeError("auth.options must be a mapping")
        return build_extension(self.registry.auth_providers[name], self.config, options)

    def _build_transformers(self) -> list[Any]:
        configured = self.config.raw.get("transformers", [])
        if configured in (None, ""):
            return []
        if not isinstance(configured, list):
            raise TypeError("transformers must be a list")
        instances: list[Any] = []
        for item in configured:
            if isinstance(item, str):
                name, options = item.casefold(), {}
            elif isinstance(item, dict):
                name = str(item.get("name", "")).strip().casefold()
                options = item.get("options", {})
                if not isinstance(options, dict):
                    raise TypeError(f"Transformer options must be a mapping: {name}")
            else:
                raise TypeError("Each transformer must be a name or mapping")
            if not name or name not in self.registry.transformers:
                raise KeyError(f"Unknown transformer plugin: {name or item}")
            instances.append(build_extension(self.registry.transformers[name], self.config, options))
        return instances

    def _processor(
        self,
        name: str,
        *,
        parser: bool = False,
        extractor: bool = False,
        config: AppConfig | None = None,
    ) -> Any:
        if parser and extractor:
            raise ValueError("An extension cannot be selected as both parser and extractor")
        bucket = (
            self.registry.parsers if parser
            else self.registry.extractors if extractor
            else self.registry.processors
        )
        kind = "parser" if parser else "extractor" if extractor else "processor"
        if config is not None:
            # B-2 闸门 per-URL 覆盖：配置与共享实例不同，走独立实例（不缓存，避免串文档）
            if name not in bucket:
                raise KeyError(f"Unknown {kind} plugin: {name}")
            options = config.section("extract").get(f"{kind}_options", {})
            if not isinstance(options, dict):
                raise TypeError(f"extract.{kind}_options must be a mapping")
            # 与下方缓存路径一致的命名 options 判定
            named_form = bool(options) and all(key in bucket for key in options)
            if named_form:
                options = options.get(name, {})
            return build_extension(bucket[name], config, options)
        cache_key = f"{kind}:{name}"
        # S2.5.41：实例缓存加锁，消除多线程 check-then-act 竞态
        with self._processor_lock:
            if cache_key not in self._processor_instances:
                if name not in bucket:
                    raise KeyError(f"Unknown {kind} plugin: {name}")
                options = self.config.section("extract").get(f"{kind}_options", {})
                if not isinstance(options, dict):
                    raise TypeError(f"extract.{kind}_options must be a mapping")
                # S2.5.41：按插件名分配独立 options——当所有键都是已注册插件名时
                # 视为 {name: {…}} 映射，未点名插件得空 options；否则视为通用 options
                named_form = bool(options) and all(key in bucket for key in options)
                if named_form:
                    options = options.get(name, {})
                self._processor_instances[cache_key] = build_extension(bucket[name], self.config, options)
            return self._processor_instances[cache_key]
