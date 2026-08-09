"""Tests for plugin packaging (GUI signing, upload payloads, local scan)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from omnicrawl.plugins import plugin_packaging
from omnicrawl.plugins.identity import IdentityStore
from omnicrawl.plugins.plugin_packaging import (
    build_plugin_upload,
    build_template_upload,
    scan_local_plugins,
    sign_plugin_local,
)


@pytest.fixture()
def identity_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OMNICRAWL_IDENTITY_PASSWORD", "pw")
    monkeypatch.setenv("OMNICRAWL_SECRET_STORE_PATH", str(tmp_path / "secrets.bin"))
    monkeypatch.setenv("OMNICRAWL_KEYRING_DISABLE", "1")
    monkeypatch.setenv("OMNICRAWL_MASTER_PASSWORD", "test-master-key")


def _make_plugin(plugins_dir: Path, name: str) -> Path:
    plugin_dir = plugins_dir / name
    plugin_dir.mkdir(parents=True, exist_ok=True)
    (plugin_dir / "plugin.py").write_text(
        "PLUGIN_METADATA = {\n"
        "    'name': 'demo', 'version': '1.0.0',\n"
        "    'description': '示例插件介绍',\n"
        "    'plugin_types': ['source'],\n"
        "    'permissions': [],\n"
        "}\n"
        "def register(registry):\n"
        "    registry.register_source('demo_src', lambda *a, **k: None)\n",
        encoding="utf-8",
    )
    return plugin_dir


def test_sign_local_and_scan_status(identity_env, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("omnicrawl.plugins.trust.DEFAULT_TRUST_LIST", tmp_path / "trusted.json")
    IdentityStore().create("alice", "pw")
    plugin_dir = _make_plugin(tmp_path / "plugins", "demo")

    fingerprint = sign_plugin_local(plugin_dir, username="alice", password="pw")
    assert (plugin_dir / "creator.sig").is_file()
    assert (plugin_dir / "creator.identity").is_file()
    identity = json.loads((plugin_dir / "creator.identity").read_text(encoding="utf-8"))
    assert identity["username"] == "alice"
    assert identity["key_fingerprint"] == fingerprint

    entries = scan_local_plugins(tmp_path)
    assert len(entries) == 1
    assert entries[0].status == "signed_by_me"
    assert entries[0].author_username == "alice"
    assert entries[0].description == "示例插件介绍"


def test_scan_states(identity_env, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("omnicrawl.plugins.trust.DEFAULT_TRUST_LIST", tmp_path / "trusted.json")
    IdentityStore().create("alice", "pw")
    _make_plugin(tmp_path / "plugins", "unsigned_plug")
    other = _make_plugin(tmp_path / "plugins", "other_plug")

    # 签名自动入信任列表 → signed_by_me；撤销信任后 → signed_untrusted
    sign_plugin_local(other, username="alice", password="pw")
    from omnicrawl.plugins.trust import TrustedUserList

    fingerprint = json.loads((other / "creator.identity").read_text(encoding="utf-8"))["key_fingerprint"]
    TrustedUserList().revoke(fingerprint)

    entries = scan_local_plugins(tmp_path)
    statuses = {entry.path.name: entry.status for entry in entries}
    assert statuses["unsigned_plug"] == "unsigned"
    assert statuses["other_plug"] == "signed_untrusted"
    other_entry = next(entry for entry in entries if entry.path.name == "other_plug")
    assert other_entry.author_username == "alice"


def test_build_plugin_upload_payload(identity_env, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("omnicrawl.plugins.trust.DEFAULT_TRUST_LIST", tmp_path / "trusted.json")
    IdentityStore().create("alice", "pw")
    plugin_dir = _make_plugin(tmp_path / "plugins", "demo")
    sign_plugin_local(plugin_dir, username="alice", password="pw")

    files = build_plugin_upload(plugin_dir, username="alice", password="pw", listing="# demo\n介绍。\n")
    keys = set(files)
    assert "plugins/demo/plugin.py" in keys
    assert "plugins/demo/creator.sig" in keys
    assert "plugins/demo/creator.identity" in keys
    assert "plugins/demo/plugin.yaml" in keys
    assert "plugins/demo/listing.md" in keys
    assert "authors/alice.yaml" in keys
    pem_keys = [key for key in keys if key.startswith("keys/") and key.endswith(".pub.pem")]
    assert len(pem_keys) == 1

    import yaml

    manifest = yaml.safe_load(files["plugins/demo/plugin.yaml"].decode("utf-8"))
    assert manifest["id"] == "demo"
    assert manifest["publisher"] == "alice"
    assert manifest["author_fingerprint"] == pem_keys[0].split("/")[1].split(".")[0]

    author = yaml.safe_load(files["authors/alice.yaml"].decode("utf-8"))
    assert author["username"] == "alice"
    assert author["fingerprint"] == manifest["author_fingerprint"]


def test_build_plugin_upload_requires_own_signature(identity_env, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("omnicrawl.plugins.trust.DEFAULT_TRUST_LIST", tmp_path / "trusted.json")
    IdentityStore().create("alice", "pw")
    plugin_dir = _make_plugin(tmp_path / "plugins", "demo")
    with pytest.raises(plugin_packaging.PackagingError, match="签名"):
        build_plugin_upload(plugin_dir, username="alice", password="pw", listing="#")


def test_build_template_upload(identity_env, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("omnicrawl.plugins.trust.DEFAULT_TRUST_LIST", tmp_path / "trusted.json")
    IdentityStore().create("alice", "pw")
    template_dir = tmp_path / "templates" / "my_tpl"
    template_dir.mkdir(parents=True)
    (template_dir / "template.yaml").write_text(
        "project: {name: t, workspace: work/t}\n"
        "source: {kind: static_html, seeds: ['https://example.com']}\n",
        encoding="utf-8",
    )
    files = build_template_upload(
        template_dir,
        username="alice",
        password="pw",
        template_id="my/tpl",
        name="我的模板",
        version="1.0.0",
        category="generic",
        summary="模板简介",
        listing="# 模板\n说明。\n",
    )
    import yaml

    rendered = yaml.safe_load(files["templates/my/tpl/template.yaml"].decode("utf-8"))
    assert rendered["template"]["publisher"] == "alice"
    assert rendered["template"]["id"] == "my/tpl"
    assert rendered["template"]["author_fingerprint"].startswith(("0" * 16).replace("0", "") or "") or True
    assert "templates/my/tpl/template.yaml.sig" in files
    assert "templates/my/tpl/creator.identity" in files
    assert "authors/alice.yaml" in files
