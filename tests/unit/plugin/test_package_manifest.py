from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from omnicrawler.core.secrets_store import SecretsStore
from omnicrawler.plugins.identity import IdentityStore
from omnicrawler.plugins.package_manifest import (
    CREATOR_SIGNATURE_NAME,
    MANIFEST_NAME,
    PackageManifestError,
    sign_creator_package,
    verify_package,
)


class _FakeKeyring:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.data.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.data[(service, username)] = password


def _identity(tmp_path: Path):
    secrets = SecretsStore(tmp_path / "secrets.bin", keyring_api=_FakeKeyring())
    return IdentityStore(store=secrets).create("alice", "pw")


def test_creator_package_roundtrip_covers_every_payload_file(tmp_path: Path) -> None:
    root = tmp_path / "demo"
    root.mkdir()
    (root / "plugin.py").write_text("def handle(op, payload): return {}\n", encoding="utf-8")
    (root / "plugin.yaml").write_text("id: demo\nversion: 1.0.0\n", encoding="utf-8")
    (root / "listing.md").write_text("# Demo\n", encoding="utf-8")

    signed = sign_creator_package(
        root,
        package_type="plugin",
        package_id="demo",
        version="1.0.0",
        identity=_identity(tmp_path),
        legacy_target="plugin.py",
    )

    assert (root / MANIFEST_NAME).is_file()
    assert (root / CREATOR_SIGNATURE_NAME).is_file()
    assert (root / "creator.sig").is_file()
    verified = verify_package(root)
    assert verified.manifest_sha256 == signed.manifest_sha256
    assert verified.creator.username == "alice"
    manifest = json.loads((root / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert set(manifest["files"]) == {
        "creator.identity",
        "listing.md",
        "plugin.py",
        "plugin.yaml",
    }


def test_package_rejects_modified_or_extra_file(tmp_path: Path) -> None:
    root = tmp_path / "demo"
    root.mkdir()
    (root / "plugin.py").write_text("def handle(op, payload): return {}\n", encoding="utf-8")
    sign_creator_package(
        root,
        package_type="plugin",
        package_id="demo",
        version="1.0.0",
        identity=_identity(tmp_path),
        legacy_target="plugin.py",
    )
    (root / "plugin.py").write_text("raise RuntimeError('changed')\n", encoding="utf-8")
    with pytest.raises(PackageManifestError, match="哈希不一致"):
        verify_package(root)

    sign_creator_package(
        root,
        package_type="plugin",
        package_id="demo",
        version="1.0.0",
        identity=_identity(tmp_path / "other"),
        legacy_target="plugin.py",
    )
    (root / "unexpected.py").write_text("pass\n", encoding="utf-8")
    with pytest.raises(PackageManifestError, match="未声明"):
        verify_package(root)


def test_package_rejects_private_material(tmp_path: Path) -> None:
    root = tmp_path / "demo"
    root.mkdir()
    (root / "template.yaml").write_text("project: {}\n", encoding="utf-8")
    (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
    with pytest.raises(PackageManifestError, match="私钥或凭据"):
        sign_creator_package(
            root,
            package_type="template",
            package_id="demo",
            version="1.0.0",
            identity=_identity(tmp_path),
            legacy_target="template.yaml",
        )
