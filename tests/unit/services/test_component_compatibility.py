from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from omnicrawler.services import component_manager, component_registry
from omnicrawler.services.component_compatibility import satisfies
from omnicrawler.services.component_manager import ComponentManager


def _package(tmp_path: Path, name: str, version: str, **metadata) -> Path:
    payload = f"{name}-{version}".encode()
    manifest = {"name": name, "version": version,
                "files": {"model.bin": hashlib.sha256(payload).hexdigest()}, **metadata}
    target = tmp_path / f"{name}-{version}.ocp"
    with zipfile.ZipFile(target, "w") as archive:
        archive.writestr("component.json", json.dumps(manifest))
        archive.writestr("model.bin", payload)
    return target


def test_version_constraints_preserve_dependents_on_upgrade_and_rollback(tmp_path):
    manager = ComponentManager(tmp_path / "components")
    manager.import_offline(_package(tmp_path, "runtime", "1.0"), allow_unsigned=True)
    manager.import_offline(_package(tmp_path, "runtime", "2.0"), allow_unsigned=True)
    manager.import_offline(_package(tmp_path, "ocr", "1.0", dependencies=["runtime>=2,<3"]), allow_unsigned=True)
    before = manager.manifest_path.read_bytes()
    with pytest.raises(ValueError, match="依赖版本"):
        manager.rollback("runtime")
    with pytest.raises(ValueError, match="依赖版本"):
        manager.import_offline(_package(tmp_path, "runtime", "3.0"), allow_unsigned=True)
    with pytest.raises(ValueError, match="仍依赖"):
        manager.uninstall("runtime")
    assert manager.manifest_path.read_bytes() == before
    assert not (manager.root / "runtime" / "3.0").exists()


@pytest.mark.parametrize("metadata", [
    {"core_version": ">=99"}, {"platforms": ["unsupported"]},
    {"architectures": ["unsupported"]}, {"dependencies": "ocr"},
    {"dependencies": ["runtime>=not-a-version"]},
])
def test_incompatible_manifest_never_activates(tmp_path, metadata):
    manager = ComponentManager(tmp_path / "components")
    with pytest.raises(ValueError):
        manager.import_offline(_package(tmp_path, "ocr", "1.0", **metadata), allow_unsigned=True)
    assert manager.list() == []
    assert not (manager.root / "ocr").exists()


def test_dependency_cycles_rejected_before_activation(tmp_path):
    manager = ComponentManager(tmp_path / "components")
    manager.import_offline(_package(tmp_path, "runtime", "1.0"), allow_unsigned=True)
    manager.import_offline(_package(tmp_path, "ocr", "1.0", dependencies=["runtime"]), allow_unsigned=True)
    with pytest.raises(ValueError, match="循环"):
        manager.import_offline(_package(tmp_path, "runtime", "2.0", dependencies=["ocr"]), allow_unsigned=True)
    assert {entry["name"]: entry["version"] for entry in manager.list()} == {"runtime": "1.0", "ocr": "1.0"}


def test_activation_failure_can_resume_same_verified_version(tmp_path, monkeypatch):
    manager = ComponentManager(tmp_path / "components")
    package = _package(tmp_path, "ocr", "1.0")
    with monkeypatch.context() as injection:
        injection.setattr(component_manager, "commit_document", lambda *_args: (_ for _ in ()).throw(OSError("interrupted")))
        with pytest.raises(OSError, match="interrupted"):
            manager.import_offline(package, allow_unsigned=True)
    assert manager.list() == []
    assert (manager.root / "ocr" / "1.0" / "model.bin").is_file()
    assert manager.import_offline(package, allow_unsigned=True)["version"] == "1.0"


def test_journal_recovers_registry_and_rollback_metadata_together(tmp_path, monkeypatch):
    manager = ComponentManager(tmp_path / "components")
    manager.import_offline(_package(tmp_path, "ocr", "1.0"), allow_unsigned=True)
    original = component_registry.atomic_write

    def interrupt(path, payload):
        if path.name == "installed.json":
            raise OSError("interrupted registry write")
        return original(path, payload)

    with monkeypatch.context() as injection:
        injection.setattr(component_registry, "atomic_write", interrupt)
        with pytest.raises(OSError):
            manager.import_offline(_package(tmp_path, "ocr", "2.0"), allow_unsigned=True)
    assert manager.list()[0]["version"] == "2.0"
    assert manager.rollback("ocr")["version"] == "1.0"
    assert not (manager.root / ".registry.pending.json").exists()


@pytest.mark.parametrize("constraint", [">=garbage", ">=1,ignored", "~=1", "==1.*"])
def test_unknown_constraint_grammar_never_succeeds(constraint):
    with pytest.raises(ValueError):
        satisfies("2.0", constraint)


def test_zero_padding_in_numeric_versions():
    assert satisfies("1", "==1.0.0")
    assert not satisfies("2.0", ">=1,<2")


def test_case_collisions_and_wrong_version_path_are_rejected(tmp_path):
    manager = ComponentManager(tmp_path / "components")
    manager.import_offline(_package(tmp_path, "ocr", "1.0"), allow_unsigned=True)
    with pytest.raises(ValueError, match="大小写"):
        manager.import_offline(_package(tmp_path, "OCR", "2.0"), allow_unsigned=True)
    raw = json.loads(manager.manifest_path.read_text(encoding="utf-8"))
    raw["ocr"]["path"] = "ocr/2.0"
    manager.manifest_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="名称版本"):
        manager.list()
