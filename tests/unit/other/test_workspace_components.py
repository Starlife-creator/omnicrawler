from __future__ import annotations

import base64
import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from omnicrawl import runtime_paths
from omnicrawl.core.config import load_config
from omnicrawl.core.runtime_manifest import create_runtime_manifest, verify_runtime_manifest
from omnicrawl.services.component_manager import ComponentManager
from omnicrawl.services.updater import UpgradeManager
from omnicrawl.services.workspace import WORKSPACE_DIRECTORIES, WorkspaceManager
from omnicrawl.state import StateStore


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "task.yaml"
    path.write_text(
        f"project: {{name: workspace, workspace: '{tmp_path / 'workspace'}'}}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    return path


def test_workspace_layout_packages_health_snapshot_and_failed_upgrade_rollback(tmp_path: Path) -> None:
    config = load_config(_config(tmp_path))
    manager = WorkspaceManager(config)
    manager.initialize()
    assert all((config.workspace / name).is_dir() for name in WORKSPACE_DIRECTORIES)
    with StateStore(config.workspace / "state.sqlite3"):
        pass
    assert manager.health()["ok"] is True
    assert manager.package(tmp_path / "config.zip", kind="config")["files"] == 1
    assert manager.package(tmp_path / "support.zip", kind="support")["files"] >= 2
    full = manager.package(tmp_path / "full.zip", kind="full")
    with zipfile.ZipFile(full["created"]) as archive:
        assert "project/workspace/state.sqlite3" in archive.namelist()
        assert "project/workspace/workspace.json" in archive.namelist()

    original = config.path.read_text(encoding="utf-8")

    def fail_upgrade() -> None:
        config.path.write_text(original.replace("workspace", "changed", 1), encoding="utf-8")
        raise RuntimeError("upgrade failed")

    with pytest.raises(RuntimeError, match="upgrade failed"):
        manager.transactional_upgrade(fail_upgrade)
    assert config.path.read_text(encoding="utf-8") == original
    assert len(list((config.workspace / "snapshots").glob("*.zip"))) >= 2


def _component_package(path: Path, *, name: str = "ocr-zh", dependencies: list[str] | None = None) -> None:
    payload = b"model-data"
    manifest = {
        "name": name, "version": "1.0", "purpose": "中文OCR", "edition": "optional",
        "disk_bytes": len(payload), "dependencies": dependencies or [],
        "uninstall_impact": "中文扫描PDF将不能OCR", "files": {"models/model.bin": hashlib.sha256(payload).hexdigest()},
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("component.json", json.dumps(manifest, ensure_ascii=False))
        archive.writestr("models/model.bin", payload)


def test_component_offline_import_hash_dependencies_and_recoverable_uninstall(tmp_path: Path) -> None:
    package = tmp_path / "ocr.ocp"
    _component_package(package)
    manager = ComponentManager(tmp_path / "components")
    with pytest.raises(ValueError, match="受信签名"):
        manager.inspect_package(package)
    installed = manager.import_offline(package, allow_unsigned=True)
    assert installed["purpose"] == "中文OCR"
    assert Path(installed["path"]).joinpath("models/model.bin").read_bytes() == b"model-data"

    dependent = tmp_path / "industry.ocp"
    _component_package(dependent, name="industry", dependencies=["ocr-zh"])
    manager.import_offline(dependent, allow_unsigned=True)
    with pytest.raises(ValueError, match="仍依赖"):
        manager.uninstall("ocr-zh")
    manager.uninstall("industry")
    removed = manager.uninstall("ocr-zh")
    assert Path(removed["recoverable_from"]).is_dir()
    assert Path(manager.rollback("ocr-zh")["path"]).is_dir()


def test_resumable_component_stage_and_runtime_manifest_detect_tampering(tmp_path: Path) -> None:
    source = tmp_path / "large.ocp"
    source.write_bytes(b"0123456789" * 1000)
    manager = ComponentManager(tmp_path / "components")
    partial = manager.root / ".downloads" / "large.ocp.partial"
    partial.parent.mkdir(parents=True)
    partial.write_bytes(source.read_bytes()[:137])
    staged = manager.stage_resumable(source, hashlib.sha256(source.read_bytes()).hexdigest(), chunk_size=97)
    assert staged["resumed_from"] == 137
    assert Path(staged["package"]).read_bytes() == source.read_bytes()

    runtime = tmp_path / "runtime"
    runtime.mkdir()
    executable = runtime / "tool.exe"
    executable.write_bytes(b"trusted")
    create_runtime_manifest(runtime)
    assert verify_runtime_manifest(runtime)["ok"] is True
    executable.write_bytes(b"tampered")
    assert verify_runtime_manifest(runtime)["corrupt"] == ["tool.exe"]


def test_signed_upgrade_stages_and_preserves_workspace_paths(tmp_path: Path) -> None:
    serialization = pytest.importorskip("cryptography.hazmat.primitives.serialization")
    ed25519 = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")

    private = ed25519.Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    payload = b"new executable"
    manifest = {"version": "1.4.1", "files": {"bin/app.exe": hashlib.sha256(payload).hexdigest()}}
    canonical = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    signed = {**manifest, "signature": base64.b64encode(private.sign(canonical)).decode()}
    package = tmp_path / "upgrade.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("upgrade.json", json.dumps(signed, ensure_ascii=False))
        archive.writestr("bin/app.exe", payload)
    app = tmp_path / "app"
    (app / "work").mkdir(parents=True)
    (app / "work" / "user.db").write_bytes(b"user-data")
    manager = UpgradeManager(app, trusted_public_key=public)
    staged = manager.stage(package)
    result = manager.apply(Path(staged["stage"]))
    assert (app / "bin" / "app.exe").read_bytes() == payload
    assert (app / "work" / "user.db").read_bytes() == b"user-data"
    assert result["workspace_preserved"] is True


def test_portable_mode_choice_placeholders_and_storage_advisory(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_paths, "application_dir", lambda: tmp_path)
    monkeypatch.setattr(runtime_paths, "is_frozen", lambda: False)
    runtime_paths.portable_data_root.cache_clear()
    assert runtime_paths.configure_data_mode("portable") == tmp_path
    selected = runtime_paths.configure_data_mode("custom", "数据 工作区")
    assert selected == (tmp_path / "数据 工作区").resolve()
    assert runtime_paths.resolve_portable_path("${DATA_DIR}/项目").parent == selected
    report = runtime_paths.storage_advisory(selected)
    assert report["path"] == str(selected)
