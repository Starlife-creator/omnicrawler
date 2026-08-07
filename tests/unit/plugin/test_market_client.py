"""Tests for the curated plugin-market client and directory-style loading."""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawl.plugins import market_client
from omnicrawl.plugins.plugins import Registry, load_local_plugins

REPO_ROOT = Path(__file__).resolve().parents[3]
TRUST = str(REPO_ROOT / "configs" / "plugin_trust.pub.pem")
REGISTRY_DIR = REPO_ROOT / "registry"


def test_fetch_catalog_local() -> None:
    catalog = market_client.fetch_catalog(str(REGISTRY_DIR))
    assert catalog["schema_version"] == 1
    ids = [entry["id"] for entry in catalog["plugins"]]
    assert "example_news" in ids


def test_download_and_verify(tmp_path: Path) -> None:
    dest = tmp_path / "installed"
    path = market_client.download_and_verify("example_news", str(REGISTRY_DIR), dest, TRUST)
    assert path.is_file()
    assert (path.parent / "plugin.py.sig").is_file()
    assert (path.parent / "listing.md").is_file()
    ok, reason = market_client.verify_installed(dest, "example_news", TRUST)
    assert ok and reason == "verified"


def test_download_rejects_tampered(tmp_path: Path) -> None:
    dest = tmp_path / "installed"
    path = market_client.download_and_verify("example_news", str(REGISTRY_DIR), dest, TRUST)
    path.write_bytes(path.read_bytes() + b"\n# tampered")
    ok, reason = market_client.verify_installed(dest, "example_news", TRUST)
    assert not ok


def test_fetch_missing_plugin_raises(tmp_path: Path) -> None:
    with pytest.raises(KeyError):
        market_client.download_and_verify("nonexistent", str(REGISTRY_DIR), tmp_path, TRUST)


def test_directory_loading_recursive(tmp_path: Path) -> None:
    for name in ("foo", "bar"):
        sub = tmp_path / name
        sub.mkdir()
        (sub / "plugin.py").write_text(
            f'PLUGIN_METADATA = {{"name": "{name}", "version": "1.0.0"}}\n'
            f"def register(registry):\n"
            f'    registry.register_source("{name}", lambda *a, **k: None)\n'
        )
    registry = Registry()
    load_local_plugins(registry, [str(tmp_path)], tmp_path, config=None)
    assert "foo" in registry.sources
    assert "bar" in registry.sources
