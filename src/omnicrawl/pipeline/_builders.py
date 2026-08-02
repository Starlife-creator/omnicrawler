"""Builder mixin: auth-provider, transformer and processor construction."""

from __future__ import annotations

from typing import Any

from ..plugins.plugin_runtime import build_extension
from ._mixin_base import _PipelineBase


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

    def _processor(self, name: str, *, parser: bool = False, extractor: bool = False) -> Any:
        if parser and extractor:
            raise ValueError("An extension cannot be selected as both parser and extractor")
        bucket = (
            self.registry.parsers if parser
            else self.registry.extractors if extractor
            else self.registry.processors
        )
        kind = "parser" if parser else "extractor" if extractor else "processor"
        cache_key = f"{kind}:{name}"
        if cache_key not in self._processor_instances:
            if name not in bucket:
                raise KeyError(f"Unknown {kind} plugin: {name}")
            options = self.config.section("extract").get(f"{kind}_options", {})
            if not isinstance(options, dict):
                raise TypeError(f"extract.{kind}_options must be a mapping")
            self._processor_instances[cache_key] = build_extension(bucket[name], self.config, options)
        return self._processor_instances[cache_key]
