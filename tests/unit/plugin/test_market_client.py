"""Tests for the curated plugin-market client and directory-style loading."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("cryptography")

from omnicrawler.plugins import market_client
from omnicrawler.plugins.plugins import Registry, load_local_plugins

REPO_ROOT = Path(__file__).resolve().parents[3]
TRUST = str(REPO_ROOT / "configs" / "plugin_trust.pub.pem")
REGISTRY_DIR = REPO_ROOT.parent / "OmniCrawler-market"

pytestmark = pytest.mark.skipif(
    not REGISTRY_DIR.is_dir(),
    reason="OmniCrawler-market 仓库未 clone（需与主仓库同级），跳过市场客户端测试",
)


@pytest.fixture
def preverified_legacy_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate legacy payload-signature tests from catalog-authentication tests.

    The sibling checkout intentionally has a stale catalog signature until the
    maintainer next uses the cold key. Catalog authentication and replay are
    covered with generated keys in test_market_catalog_verification.py.
    """
    raw_fetch = market_client.fetch_catalog
    monkeypatch.setattr(
        market_client,
        "fetch_catalog_verified",
        lambda url, _trust, **kwargs: raw_fetch(
            url, timeout=kwargs.get("timeout", market_client.DEFAULT_TIMEOUT),
            egress=kwargs.get("egress")
        ),
    )


def test_fetch_catalog_local() -> None:
    catalog = market_client.fetch_catalog(str(REGISTRY_DIR))
    assert catalog["schema_version"] == 1
    ids = [entry["id"] for entry in catalog["plugins"]]
    assert "example_news" in ids


def test_download_and_verify(tmp_path: Path, preverified_legacy_catalog: None) -> None:
    dest = tmp_path / "installed"
    path = market_client.download_and_verify("example_news", str(REGISTRY_DIR), dest, TRUST)
    assert path.is_file()
    assert (path.parent / "plugin.py.sig").is_file()
    assert (path.parent / "listing.md").is_file()
    assert (path.parent / market_client._INSTALL_META).is_file()
    ok, reason = market_client.verify_installed(dest, "example_news", TRUST)
    assert ok and reason == "verified"


def test_install_writes_hash_lockfile(tmp_path: Path, preverified_legacy_catalog: None) -> None:
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


def test_verify_rejects_hash_tamper_before_signature(
    tmp_path: Path, preverified_legacy_catalog: None
) -> None:
    dest = tmp_path / "installed"
    path = market_client.download_and_verify("example_news", str(REGISTRY_DIR), dest, TRUST)
    path.write_bytes(path.read_bytes() + b"\n# tampered")
    ok, reason = market_client.verify_installed(dest, "example_news", TRUST)
    assert not ok
    assert "哈希" in reason


def test_verify_falls_back_to_signature_without_lockfile(
    tmp_path: Path, preverified_legacy_catalog: None
) -> None:
    dest = tmp_path / "installed"
    market_client.download_and_verify("example_news", str(REGISTRY_DIR), dest, TRUST)
    (dest / "example_news" / market_client._INSTALL_META).unlink()
    ok, reason = market_client.verify_installed(dest, "example_news", TRUST)
    assert ok and reason == "verified"


def test_download_rejects_tampered(tmp_path: Path, preverified_legacy_catalog: None) -> None:
    dest = tmp_path / "installed"
    path = market_client.download_and_verify("example_news", str(REGISTRY_DIR), dest, TRUST)
    path.write_bytes(path.read_bytes() + b"\n# tampered")
    ok, reason = market_client.verify_installed(dest, "example_news", TRUST)
    assert not ok


def test_fetch_missing_plugin_raises(tmp_path: Path, preverified_legacy_catalog: None) -> None:
    with pytest.raises(KeyError):
        market_client.download_and_verify("nonexistent", str(REGISTRY_DIR), tmp_path, TRUST)


def test_download_template_and_verify(tmp_path: Path) -> None:
    """模板市场：构造含 templates 条目的本地 catalog → 下载验签安装。"""
    import hashlib
    import json

    from omnicrawler.plugins import signing

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
    catalog_bytes = json.dumps(
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
        ).encode("utf-8")
    (registry / "catalog.json").write_bytes(catalog_bytes)
    (registry / "catalog.json.sig").write_bytes(signing.sign_bytes(catalog_bytes, private_pem))

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

    protected_root = tmp_path / "protected_templates"
    protected = protected_root / "demo" / "template"
    protected.mkdir(parents=True)
    manifest = protected / "package.manifest.json"
    manifest.write_text('{"package_format": 1}', encoding="utf-8")
    with pytest.raises(PermissionError, match="完整签名包"):
        market_client.download_template_and_verify(
            "demo/template", str(registry), protected_root, str(trust)
        )
    assert manifest.read_text(encoding="utf-8") == '{"package_format": 1}'
    assert not (protected / "template.yaml").exists()


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
    load_local_plugins(registry, [str(tmp_path)], tmp_path, config=None, signature_policy="developer")
    assert "foo" in registry.sources
    assert "bar" in registry.sources


def test_directory_loading_records_unreadable_plugin_without_hiding_siblings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    denied = tmp_path / "denied"
    denied.mkdir()

    def fake_walk(root, *, topdown, onerror, followlinks):
        assert Path(root) == tmp_path
        assert topdown and not followlinks
        onerror(PermissionError(13, "denied", str(denied)))
        return iter(())

    monkeypatch.setattr("omnicrawler.plugins.plugins.os.walk", fake_walk)
    registry = Registry()
    load_local_plugins(registry, [str(tmp_path)], tmp_path, fail_open=True)

    assert len(registry.plugin_errors) == 1
    assert registry.plugin_errors[0]["path"] == str(denied)
    assert "PermissionError" in registry.plugin_errors[0]["error"]


def test_complete_package_uses_inherited_acl_sibling_staging(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from omnicrawler.plugins import identity, package_manifest

    manifest_bytes = json.dumps(
        {
            "package_id": "demo",
            "version": "1.0.0",
            "files": {"plugin.py": "sha256:ignored"},
        },
        separators=(",", ":"),
    ).encode()
    expected_hash = hashlib.sha256(manifest_bytes).hexdigest()
    resources = {
        "plugins/demo/package.manifest.json": manifest_bytes,
        "plugins/demo/package.manifest.creator.sig": b"creator-package",
        "plugins/demo/package.manifest.maintainer.sig": b"maintainer-package",
        "plugins/demo/creator.sig": b"creator",
        "plugins/demo/plugin.py.sig": b"plugin",
        "plugins/demo/plugin.py": b"PLUGIN_METADATA = {}\n",
    }
    entry = {
        "id": "demo",
        "version": "1.0.0",
        "package_manifest_file": "plugins/demo/package.manifest.json",
        "creator_package_signature_file": "plugins/demo/package.manifest.creator.sig",
        "maintainer_package_signature_file": "plugins/demo/package.manifest.maintainer.sig",
        "creator_signature_file": "plugins/demo/creator.sig",
        "signature_file": "plugins/demo/plugin.py.sig",
        "package_manifest_sha256": expected_hash,
    }
    monkeypatch.setattr(
        market_client,
        "fetch_resource",
        lambda _base, rel, **_kwargs: resources[rel],
    )
    monkeypatch.setattr(identity, "public_key_bytes_from_pem", lambda _value: b"trust")
    monkeypatch.setattr(
        package_manifest,
        "verify_package",
        lambda *_args, **_kwargs: SimpleNamespace(
            manifest_sha256=expected_hash, package_id="demo", version="1.0.0"
        ),
    )
    monkeypatch.setattr(
        market_client.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="fixed"),
    )

    destination = tmp_path / "installed" / "demo"
    result = market_client._download_manifest_package(
        entry, "market", destination, "trust", main_name="plugin.py", timeout=1, egress=None
    )

    assert result == destination / "plugin.py"
    assert result.read_bytes() == resources["plugins/demo/plugin.py"]
    assert not (destination.parent / ".demo.staging-fixed").exists()


# ── P9-B1（B01-011）：egress=None 拒绝出网 ─────────────────────────


def test_remote_fetch_without_egress_is_blocked() -> None:
    """远程资源在缺少 egress 时 fail-closed 拒绝（不裸 urlopen 出网）。"""
    from omnicrawler.core.errors import PolicyBlockedError
    from omnicrawler.plugins.market_client import _read

    with pytest.raises(PolicyBlockedError, match="缺少出口策略"):
        _read("https://example.com/catalog.json")


def test_local_fetch_still_works_without_egress(tmp_path: Path) -> None:
    """本地文件读取不受影响（无网络出口）。"""
    from omnicrawler.plugins.market_client import _read

    f = tmp_path / "local.json"
    f.write_text("{}", encoding="utf-8")
    assert _read(str(f)) == b"{}"
