"""Tests for the curated plugin-market client and directory-style loading."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from omnicrawl.plugins import market_client
from omnicrawl.plugins.plugins import Registry, load_local_plugins

REPO_ROOT = Path(__file__).resolve().parents[3]
TRUST = str(REPO_ROOT / "configs" / "plugin_trust.pub.pem")
REGISTRY_DIR = REPO_ROOT.parent / "OmniCrawler-market"

pytestmark = pytest.mark.skipif(
    not REGISTRY_DIR.is_dir(),
    reason="OmniCrawler-market 仓库未 clone（需与主仓库同级），跳过市场客户端测试",
)


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
    assert (path.parent / market_client._INSTALL_META).is_file()
    ok, reason = market_client.verify_installed(dest, "example_news", TRUST)
    assert ok and reason == "verified"


def test_install_writes_hash_lockfile(tmp_path: Path) -> None:
    import hashlib
    import json

    dest = tmp_path / "installed"
    market_client.download_and_verify("example_news", str(REGISTRY_DIR), dest, TRUST)
    plugin_dir = dest / "example_news"
    meta = json.loads((plugin_dir / market_client._INSTALL_META).read_text(encoding="utf-8"))
    assert meta["plugin_id"] == "example_news"
    assert meta["plugin_file"] == "plugin.py"
    assert meta["plugin_sha256"] == hashlib.sha256((plugin_dir / "plugin.py").read_bytes()).hexdigest()
    assert meta["signature_sha256"] == hashlib.sha256((plugin_dir / "plugin.py.sig").read_bytes()).hexdigest()


def test_verify_rejects_hash_tamper_before_signature(tmp_path: Path) -> None:
    dest = tmp_path / "installed"
    path = market_client.download_and_verify("example_news", str(REGISTRY_DIR), dest, TRUST)
    path.write_bytes(path.read_bytes() + b"\n# tampered")
    ok, reason = market_client.verify_installed(dest, "example_news", TRUST)
    assert not ok
    assert "哈希" in reason


def test_verify_falls_back_to_signature_without_lockfile(tmp_path: Path) -> None:
    dest = tmp_path / "installed"
    market_client.download_and_verify("example_news", str(REGISTRY_DIR), dest, TRUST)
    (dest / "example_news" / market_client._INSTALL_META).unlink()
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


def test_download_template_and_verify(tmp_path: Path) -> None:
    """模板市场：构造含 templates 条目的本地 catalog → 下载验签安装。"""
    import hashlib
    import json

    from omnicrawl.plugins import signing

    private_pem, public_pem = signing.generate_keypair()
    trust = tmp_path / "trust.pub.pem"
    trust.write_bytes(public_pem)
    fingerprint = hashlib.sha256(public_pem).hexdigest()[:32]

    registry = tmp_path / "market"
    template_dir = registry / "templates" / "demo" / "template"
    template_dir.mkdir(parents=True)
    template_path = template_dir / "template.yaml"
    template_path.write_text(
        "template_version: 1\n"
        "template:\n"
        "  id: demo/template\n"
        "  name: Demo Template\n"
        "  category: generic\n"
        "  version: 1.0.0\n"
        "  publisher: alice\n"
        f"  author_fingerprint: {fingerprint}\n"
        "project: {name: demo, workspace: work/demo}\n"
        "source: {kind: static_html, seeds: ['https://example.com']}\n",
        encoding="utf-8",
    )
    signature = signing.sign_bytes(template_path.read_bytes(), private_pem)
    (template_dir / "template.yaml.sig").write_bytes(signature)
    (template_dir / "listing.md").write_text("# demo\n模板说明。\n", encoding="utf-8")
    (registry / "catalog.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugins": [],
                "templates": [
                    {
                        "id": "demo/template",
                        "name": "Demo Template",
                        "version": "1.0.0",
                        "publisher": "alice",
                        "category": "generic",
                        "summary": "测试模板",
                        "template_file": "templates/demo/template/template.yaml",
                        "signature_file": "templates/demo/template/template.yaml.sig",
                        "description_file": "templates/demo/template/listing.md",
                        "signature_algorithm": "ed25519",
                        "compatible_core": ">=2.7.0",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    dest = tmp_path / "templates_installed"
    path = market_client.download_template_and_verify("demo/template", str(registry), dest, str(trust))
    assert path.is_file()
    assert path.name == "template.yaml"
    assert (path.parent / "template.yaml.sig").is_file()
    assert (path.parent / "listing.md").is_file()
    assert (path.parent / market_client._INSTALL_META).is_file()
    ok, reason = market_client.verify_installed_template(dest, "demo/template", str(trust))
    assert ok and reason == "verified"

    # 篡改后校验失败（fail-closed）
    path.write_bytes(path.read_bytes() + b"\n# tampered")
    ok, _ = market_client.verify_installed_template(dest, "demo/template", str(trust))
    assert not ok


def test_download_template_rejects_unsafe_id(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        market_client.download_template_and_verify("../evil", str(REGISTRY_DIR), tmp_path, TRUST)


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
