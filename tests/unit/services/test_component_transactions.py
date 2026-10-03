from __future__ import annotations

import base64
import hashlib
import json
import multiprocessing
import shutil
import zipfile
from pathlib import Path

import pytest

from omnicrawler.services.component_manager import ComponentManager


def package(path: Path, name: str, version: str = "1.0", *, dependencies: tuple[str, ...] = ()) -> Path:
    payload = version.encode()
    manifest = {"name": name, "version": version, "files": {"model.bin": hashlib.sha256(payload).hexdigest()}, "dependencies": dependencies}
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("component.json", json.dumps(manifest))
        archive.writestr("model.bin", payload)
    return path


def install_process(root: str, archive: str) -> None:
    ComponentManager(Path(root)).import_offline(Path(archive), allow_unsigned=True)


def test_distinct_process_commits_preserve_both_components(tmp_path: Path) -> None:
    root = tmp_path / "components"
    context = multiprocessing.get_context("spawn")
    workers = [context.Process(target=install_process, args=(str(root), str(package(tmp_path / f"{name}.ocp", name)))) for name in ("ocr", "browser")]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(20)
        if worker.is_alive():
            worker.terminate()
            worker.join(5)
        assert worker.exitcode == 0
    assert {entry["name"] for entry in ComponentManager(root).list()} == {"ocr", "browser"}


def test_rollback_restores_metadata_and_survives_move(tmp_path: Path) -> None:
    root = tmp_path / "components"
    manager = ComponentManager(root)
    manager.import_offline(package(tmp_path / "v1.ocp", "ocr"), allow_unsigned=True)
    manager.import_offline(package(tmp_path / "v2.ocp", "ocr", "2.0"), allow_unsigned=True)
    moved = tmp_path / "moved"
    shutil.move(str(root), str(moved))
    manager = ComponentManager(moved)
    restored = manager.rollback("ocr")
    assert restored["version"] == "1.0"
    assert (Path(restored["path"]) / "model.bin").read_bytes() == b"1.0"
    manager.uninstall("ocr")
    assert manager.rollback("ocr")["files"] == restored["files"]


@pytest.mark.parametrize("name", ["../escape", "C:escape", "CON", "bad\\name"])
def test_package_identity_cannot_escape_root(tmp_path: Path, name: str) -> None:
    manager = ComponentManager(tmp_path / "components")
    with pytest.raises(ValueError, match="不安全"):
        manager.import_offline(package(tmp_path / "bad.ocp", name), allow_unsigned=True)


def test_interrupted_registry_commit_is_replayed(tmp_path: Path) -> None:
    manager = ComponentManager(tmp_path / "components")
    manager.import_offline(package(tmp_path / "ocr.ocp", "ocr"), allow_unsigned=True)
    pending = manager.root / ".registry.pending.json"
    pending.write_bytes(manager.manifest_path.read_bytes())
    manager.manifest_path.write_text("{}", encoding="utf-8")
    assert manager.list()[0]["name"] == "ocr"
    assert not pending.exists()


def test_corrupt_registry_is_not_silently_overwritten(tmp_path: Path) -> None:
    manager = ComponentManager(tmp_path / "components")
    manager.manifest_path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ValueError):
        manager.import_offline(package(tmp_path / "ocr.ocp", "ocr"), allow_unsigned=True)
    assert manager.manifest_path.read_text(encoding="utf-8") == "{broken"


def test_command_import_uses_explicit_trusted_public_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from omnicrawler.commands import components

    monkeypatch.setattr(components, "portable_data_root", lambda: tmp_path)
    key = Ed25519PrivateKey.generate()
    trust = tmp_path / ".omnicrawler" / "component_trust.pub.pem"
    trust.parent.mkdir()
    trust.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    archive_path = package(tmp_path / "signed.ocp", "ocr")
    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("component.json"))
        payload = archive.read("model.bin")
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    manifest["signature"] = base64.b64encode(key.sign(canonical)).decode()
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("component.json", json.dumps(manifest))
        archive.writestr("model.bin", payload)
    assert components.execute("import", package=str(archive_path))["name"] == "ocr"
    trust.write_bytes(Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    with pytest.raises(InvalidSignature):
        components.execute("inspect", package=str(archive_path))
