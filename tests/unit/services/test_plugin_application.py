from types import SimpleNamespace

import pytest

from omnicrawler.plugins import market_client, plugin_inspector
from omnicrawler.services.plugin_application import (
    PluginActivationPreview,
    PluginApplicationError,
    PluginApplicationService,
)


def _inspection(*, artifact_sha256: str = "a" * 64) -> SimpleNamespace:
    return SimpleNamespace(
        name="demo",
        version="2.0.0",
        artifact_sha256=artifact_sha256,
        creator_fingerprint="creator-1",
        permissions=("network:scoped",),
        compatible=True,
        errors=(),
    )


def test_preview_requires_signature_and_compatible_plugin(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    service = PluginApplicationService(tmp_path, "trust.pem")
    monkeypatch.setattr(market_client, "verify_installed", lambda *_args: (False, "bad signature"))

    with pytest.raises(PluginApplicationError, match="签名复核未通过"):
        service.preview_activation("demo")

    monkeypatch.setattr(market_client, "verify_installed", lambda *_args: (True, "verified"))
    incompatible = _inspection()
    incompatible.compatible = False
    incompatible.errors = ("api 不兼容",)
    monkeypatch.setattr(plugin_inspector, "inspect_plugin", lambda _path: incompatible)
    with pytest.raises(PluginApplicationError, match="api 不兼容"):
        service.preview_activation("demo")


def test_authorize_activation_rechecks_the_bound_payload(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    service = PluginApplicationService(tmp_path, "trust.pem")
    monkeypatch.setattr(market_client, "verify_installed", lambda *_args: (True, "verified"))
    inspections = iter((_inspection(), _inspection(artifact_sha256="b" * 64)))
    monkeypatch.setattr(plugin_inspector, "inspect_plugin", lambda _path: next(inspections))

    preview = service.preview_activation("demo")
    with pytest.raises(PluginApplicationError, match="确认期间发生变化"):
        service.authorize_activation(preview)


def test_project_config_transforms_are_copied_and_scoped() -> None:
    original = {
        "paths": ["plugins/"],
        "enabled_market_plugins": ["keep"],
        "permission_grants": {"keep": {"permissions": []}},
        "extension": {"retained": True},
    }
    preview = PluginActivationPreview(
        plugin_id="demo",
        name="demo",
        version="2.0.0",
        artifact_sha256="a" * 64,
        creator_fingerprint="creator-1",
        permissions=("network:scoped",),
    )

    enabled = PluginApplicationService.enable_project_plugin(original, preview)
    assert original["enabled_market_plugins"] == ["keep"]
    assert enabled["enabled_market_plugins"] == ["demo", "keep"]
    assert enabled["permission_grants"]["demo"]["permissions"] == ["network:scoped"]
    assert enabled["paths"] == ["plugins/", "plugins_installed/"]
    assert enabled["extension"] == {"retained": True}

    disabled = PluginApplicationService.disable_project_plugin(enabled, "demo")
    assert disabled["enabled_market_plugins"] == ["keep"]
    assert "demo" not in disabled["permission_grants"]


def test_mark_install_keeps_new_plugin_disabled_and_rejects_bad_config() -> None:
    updated = PluginApplicationService.mark_installed_plugin(
        None, "demo", installed_ids=("legacy", "demo")
    )
    assert updated["enabled_market_plugins"] == ["legacy"]

    with pytest.raises(PluginApplicationError, match="配置段结构异常"):
        PluginApplicationService.disable_project_plugin([], "demo")  # type: ignore[arg-type]
