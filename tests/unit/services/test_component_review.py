import base64
import hashlib
import json
import zipfile
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from omnicrawler.services import component_manager, component_tools
from omnicrawler.services.component_manager import ComponentManager


@pytest.fixture
def signed(tmp_path, monkeypatch):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    manager = ComponentManager(tmp_path / "components", trusted_public_key=public)
    monkeypatch.setattr(component_tools, "manager", lambda: manager)
    payload = b"local-model-evidence"
    manifest = {"name": "ocr", "version": "1.0", "purpose": "中文OCR", "disk_bytes": 0,
                "uninstall_impact": "扫描页识别不可用", "files": {"model.bin": hashlib.sha256(payload).hexdigest()}}
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    manifest["signature"] = base64.b64encode(private.sign(canonical)).decode()
    package = tmp_path / "离线 组件.ocp"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("component.json", json.dumps(manifest, ensure_ascii=False))
        archive.writestr("model.bin", payload)
    return manager, package, payload


def test_verified_payload_size_cannot_be_understated(signed):
    manager, package, payload = signed
    assert manager.inspect_package(package).disk_bytes >= len(payload)


def test_review_install_uninstall_restore_uses_exact_reviewed_state(signed):
    manager, package, _ = signed
    review = component_tools.execute("inspect", {"package": str(package)})
    assert review["signature_status"] == "trusted" and review["compatible"]
    args = {"package": str(package), "package_sha256": review["package_sha256"],
            "registry_sha256": review["registry_sha256"], "confirmed": True}
    with pytest.raises(ValueError, match="确认"):
        component_tools.execute("import", {**args, "confirmed": False})
    original = package.read_bytes()
    package.write_bytes(original + b"changed")
    with pytest.raises(ValueError, match="组件包已变化"):
        component_tools.execute("import", args)
    assert manager.list() == []
    package.write_bytes(original)
    installed = component_tools.execute("import", args)
    assert installed["component"]["name"] == "ocr"
    with pytest.raises(ValueError, match="状态已变化"):
        manager.uninstall("ocr", expected_registry_sha256=review["registry_sha256"])
    removed = component_tools.execute("uninstall", {"name": "ocr", "confirmed": True,
        "registry_sha256": installed["registry_sha256"]})
    assert manager.list() == []
    restored = component_tools.execute("rollback", {"name": "ocr", "confirmed": True,
        "registry_sha256": removed["registry_sha256"]})
    assert restored["component"]["version"] == "1.0"


def test_disk_shortage_fails_before_publication(signed, monkeypatch):
    manager, package, _ = signed
    monkeypatch.setattr(component_manager.shutil, "disk_usage", lambda _: SimpleNamespace(free=0))
    with pytest.raises(OSError, match="磁盘空间不足"):
        manager.import_offline(package)
    assert manager.list() == []
    assert not (manager.root / "ocr").exists()
