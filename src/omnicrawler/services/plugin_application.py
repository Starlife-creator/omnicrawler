"""Qt-free application service for the curated plugin lifecycle.

The GUI owns prompts and navigation.  This module owns the security checks and
the project-scoped configuration transformations so those rules can be tested
without constructing a Qt application.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..plugins import market_client, plugin_inspector
from ..plugins.plugin_sdk import validate_plugin_id


class PluginApplicationError(ValueError):
    """A plugin cannot be verified or its project configuration is invalid."""


@dataclass(frozen=True, slots=True)
class PluginActivationPreview:
    """User-visible, immutable facts that are bound by an authorization."""

    plugin_id: str
    name: str
    version: str
    artifact_sha256: str
    creator_fingerprint: str
    permissions: tuple[str, ...]


class PluginApplicationService:
    """Verify installed market plugins and prepare project-scoped changes."""

    def __init__(self, dest_root: str | Path, trust_source: str) -> None:
        self.dest_root = Path(dest_root)
        self.trust_source = trust_source

    def preview_activation(self, plugin_id: str) -> PluginActivationPreview:
        """Verify and inspect a plugin before the GUI asks for authorization."""
        try:
            validate_plugin_id(plugin_id)
        except ValueError as exc:
            raise PluginApplicationError(str(exc)) from exc

        ok, reason = market_client.verify_installed(self.dest_root, plugin_id, self.trust_source)
        if not ok:
            raise PluginApplicationError(f"签名复核未通过：{reason}")
        inspection = plugin_inspector.inspect_plugin(self.dest_root / plugin_id / "plugin.py")
        if not inspection.compatible:
            detail = "；".join(inspection.errors) or "插件不兼容"
            raise PluginApplicationError(detail)
        return PluginActivationPreview(
            plugin_id=plugin_id,
            name=str(inspection.name or plugin_id),
            version=str(inspection.version),
            artifact_sha256=str(inspection.artifact_sha256),
            creator_fingerprint=str(inspection.creator_fingerprint),
            permissions=tuple(str(item) for item in inspection.permissions),
        )

    def authorize_activation(self, preview: PluginActivationPreview) -> PluginActivationPreview:
        """Re-verify the exact payload after the user confirms the prompt."""
        current = self.preview_activation(preview.plugin_id)
        if current != preview:
            raise PluginApplicationError("插件文件或授权信息在确认期间发生变化，请重新确认")
        return current

    @staticmethod
    def enable_project_plugin(
        plugins: Mapping[str, Any] | None,
        preview: PluginActivationPreview,
        *,
        default_enabled: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Return a copied plugin section with one market plugin authorized."""
        result = _copy_plugins(plugins)
        enabled = _enabled_plugins(result, default_enabled=default_enabled)
        enabled.add(preview.name)
        result["enabled_market_plugins"] = sorted(enabled)

        grants_value = result.get("permission_grants")
        grants: dict[str, Any] = (
            deepcopy(dict(grants_value)) if isinstance(grants_value, Mapping) else {}
        )
        grants[preview.name] = {
            "version": preview.version,
            "artifact_sha256": preview.artifact_sha256,
            "creator_fingerprint": preview.creator_fingerprint,
            "permissions": list(preview.permissions),
        }
        result["permission_grants"] = grants
        result.pop("approved_permissions", None)

        paths = result.get("paths")
        if not isinstance(paths, list):
            result["paths"] = ["plugins/", "plugins_installed/"]
        elif not any(str(path).replace("\\", "/").rstrip("/") == "plugins_installed" for path in paths):
            result["paths"] = [*paths, "plugins_installed/"]
        return result

    @staticmethod
    def mark_installed_plugin(
        plugins: Mapping[str, Any] | None,
        plugin_id: str,
        *,
        installed_ids: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Record installation while keeping a newly downloaded plugin disabled."""
        result = _copy_plugins(plugins)
        enabled = _enabled_plugins(result, default_enabled=installed_ids)
        enabled.discard(plugin_id)
        result["enabled_market_plugins"] = sorted(enabled)
        grants = result.get("permission_grants")
        if isinstance(grants, Mapping):
            copied_grants = deepcopy(dict(grants))
            copied_grants.pop(plugin_id, None)
            result["permission_grants"] = copied_grants
        return result

    @staticmethod
    def disable_project_plugin(
        plugins: Mapping[str, Any] | None,
        plugin_id: str,
        *,
        default_enabled: Iterable[str] = (),
    ) -> dict[str, Any]:
        """Return a copied plugin section with project access revoked."""
        result = _copy_plugins(plugins)
        enabled = _enabled_plugins(result, default_enabled=default_enabled)
        enabled.discard(plugin_id)
        result["enabled_market_plugins"] = sorted(enabled)
        grants = result.get("permission_grants")
        if isinstance(grants, Mapping):
            copied_grants = deepcopy(dict(grants))
            copied_grants.pop(plugin_id, None)
            result["permission_grants"] = copied_grants
        return result


def _copy_plugins(plugins: Mapping[str, Any] | None) -> dict[str, Any]:
    if plugins is None:
        return {}
    if not isinstance(plugins, Mapping):
        raise PluginApplicationError("插件配置段结构异常")
    return deepcopy(dict(plugins))


def _enabled_plugins(result: Mapping[str, Any], *, default_enabled: Iterable[str]) -> set[str]:
    configured = result.get("enabled_market_plugins")
    if isinstance(configured, list):
        return {str(item) for item in configured}
    return {str(item) for item in default_enabled}
