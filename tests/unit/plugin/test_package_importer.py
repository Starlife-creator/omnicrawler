from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from omnicrawler.core.secrets_store import SecretsStore
from omnicrawler.plugins.identity import IdentityStore
from omnicrawler.plugins.package_importer import (
    PackageImportError,
    import_package_folder,
    inspect_package,
)
from omnicrawler.plugins.package_manifest import sign_creator_package


class _FakeKeyring:
    def __init__(self) -> None:
        self.data: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.data.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.data[(service, username)] = password


def _package(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    (root / "plugin.py").write_text("def handle(op, payload): return {}\n", encoding="utf-8")
    (root / "plugin.yaml").write_text(
        "id: demo\nversion: 1.0.0\npermissions: [network:scoped]\n"
        "domains: [api.example.com]\n",
        encoding="utf-8",
    )
    store = IdentityStore(
        store=SecretsStore(tmp_path / "secrets.bin", keyring_api=_FakeKeyring())
    )
    identity = store.create("alice", "pw")
    sign_creator_package(
        root,
        package_type="plugin",
        package_id="demo",
        version="1.0.0",
        identity=identity,
        legacy_target="plugin.py",
    )
    return root


def test_p2p_import_requires_permissions_and_records_provenance(tmp_path: Path) -> None:
    package = _package(tmp_path)
    inspection = inspect_package(package)
    assert inspection.permissions == ("network:scoped",)
    with pytest.raises(PackageImportError, match="尚未"):
        import_package_folder(
            package, tmp_path / "shared", source="p2p", approved_permissions=set()
        )
    installed, result = import_package_folder(
        package,
        tmp_path / "shared",
        source="p2p",
        approved_permissions={"network:scoped"},
    )
    assert installed.is_dir()
    provenance = tmp_path / "shared" / ".provenance" / f"{result.manifest_sha256}.json"
    assert provenance.is_file()


def test_market_import_requires_maintainer_signature(tmp_path: Path) -> None:
    package = _package(tmp_path)
    with pytest.raises(Exception, match="维护者签名"):
        inspect_package(package, source="market")
