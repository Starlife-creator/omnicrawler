from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from omnicrawler.pdfx.component_ocr import ComponentOCRBackend
from omnicrawler.services.component_manager import ComponentManager
from omnicrawler.services.component_registry import registry_lock
from omnicrawler.services.component_runtime import leased_ocr_runtime


def _install(tmp_path, *, version="1.0", manager=None, key=None):
    key = key or Ed25519PrivateKey.generate()
    manager = manager or ComponentManager(tmp_path / "components", trusted_public_key=key.public_key().public_bytes_raw())
    payload = version.encode()
    manifest = {"name": "ocr-paddle", "version": version,
                "files": {"runtime.exe": hashlib.sha256(payload).hexdigest()},
                "runtime": {"protocol": "ocr-file-v1", "entrypoint": "runtime.exe",
                            "engine": "paddle", "model_license": "Apache-2.0"}}
    manifest["signature"] = base64.b64encode(key.sign(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())).decode()
    package = tmp_path / f"{version}.ocp"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("component.json", json.dumps(manifest))
        archive.writestr("runtime.exe", payload)
    manager.import_offline(package)
    return manager, key


def test_runtime_reverifies_signed_metadata_and_every_file(tmp_path):
    manager, key = _install(tmp_path)
    with leased_ocr_runtime("ocr-paddle", engine="paddle", manager=manager) as runtime:
        assert runtime.version == "1.0"
    entry = manager.list()[0]
    path = Path(entry["path"]) / "runtime.exe"
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="哈希"):
        with leased_ocr_runtime("ocr-paddle", engine="paddle", manager=manager):
            pass
    # Altering installed.json hashes does not authorize modified bytes.
    installed = json.loads(manager.manifest_path.read_text(encoding="utf-8"))
    installed["ocr-paddle"]["files"]["runtime.exe"] = hashlib.sha256(b"changed").hexdigest()
    manager.manifest_path.write_text(json.dumps(installed), encoding="utf-8")
    with pytest.raises(ValueError, match="受信版本"):
        with leased_ocr_runtime("ocr-paddle", engine="paddle", manager=manager):
            pass


def test_runtime_lease_prevents_component_transaction_and_survives_move_rollback(tmp_path):
    manager, key = _install(tmp_path)
    _install(tmp_path, version="2.0", manager=manager, key=key)
    with leased_ocr_runtime("ocr-paddle", engine="paddle", manager=manager):
        with pytest.raises(TimeoutError):
            with registry_lock(manager.root, timeout=0):
                pass
    moved = tmp_path / "moved"
    shutil.move(str(manager.root), moved)
    manager = ComponentManager(moved, trusted_public_key=key.public_key().public_bytes_raw())
    manager.rollback("ocr-paddle")
    with leased_ocr_runtime("ocr-paddle", engine="paddle", manager=manager) as runtime:
        assert runtime.version == "1.0"
        assert moved in runtime.executable.parents
    manager.uninstall("ocr-paddle")
    with pytest.raises(ValueError, match="未安装"):
        with leased_ocr_runtime("ocr-paddle", engine="paddle", manager=manager):
            pass


def test_revoked_signature_cannot_run_even_when_registry_is_intact(tmp_path):
    manager, _key = _install(tmp_path)
    manager.trusted_public_key = Ed25519PrivateKey.generate().public_key().public_bytes_raw()
    with pytest.raises(InvalidSignature):
        with leased_ocr_runtime("ocr-paddle", engine="paddle", manager=manager):
            pass


def test_unlisted_dll_cannot_enter_the_component_runtime(tmp_path):
    manager, _key = _install(tmp_path)
    directory = Path(manager.list()[0]["path"])
    (directory / "unlisted.dll").write_bytes(b"loader injection")
    with pytest.raises(ValueError, match="文件集合"):
        with leased_ocr_runtime("ocr-paddle", engine="paddle", manager=manager):
            pass


def test_actual_backend_protocol_binds_input_and_keeps_host_credentials_private(tmp_path, monkeypatch):
    manager, _key = _install(tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-component")
    monkeypatch.setenv("HTTP_PROXY", "must-not-reach-component")
    seen = []

    def execute(command, **kwargs):
        seen.append((command, kwargs))
        request = json.loads(Path(command[2]).read_text(encoding="utf-8"))
        Path(command[4]).write_text(json.dumps({"format": 1, "input_sha256": request["input_sha256"], "text": "recognized", "confidence": .9}), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", execute)
    backend = ComponentOCRBackend({"component": "ocr-paddle", "backend": "paddle"}, manager=manager)
    assert backend.recognize(b"\x89PNG\r\n\x1a\nfixture") == ("recognized", .9)
    command, options = seen[0]
    assert "--offline" in command
    assert "OPENAI_API_KEY" not in options["env"] and "HTTP_PROXY" not in options["env"]
    assert options["stdin"] is subprocess.DEVNULL
    assert options["timeout"] == 120


def test_backend_rejects_wrong_input_result_and_changed_version(tmp_path, monkeypatch):
    manager, key = _install(tmp_path)
    backend = ComponentOCRBackend({"component": "ocr-paddle", "backend": "paddle"}, manager=manager)

    def execute(command, **kwargs):
        Path(command[4]).write_text(json.dumps({"format": 1, "input_sha256": "wrong", "text": "stale"}), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(subprocess, "run", execute)
    with pytest.raises(ValueError, match="输入或协议"):
        backend.recognize(b"\x89PNG\r\n\x1a\nfixture")
    _install(tmp_path, version="2.0", manager=manager, key=key)
    with pytest.raises(RuntimeError, match="版本已改变"):
        backend.recognize(b"\x89PNG\r\n\x1a\nfixture")


def test_component_backend_preserves_structured_result(tmp_path, monkeypatch):
    manager, _key = _install(tmp_path)
    def execute(command, **kwargs):
        request = json.loads(Path(command[2]).read_text(encoding="utf-8"))
        Path(command[4]).write_text(json.dumps({"format": 1, "input_sha256": request["input_sha256"],
            "text": "A", "confidence": .8, "structure": {
                "words": [{"text": "A", "confidence": .8, "bbox": [0, 0, 10, 10]}],
                "metadata": {"coordinate_system": "image_pixels_top_left", "original_mapping": "identity"}}}), encoding="utf-8")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess, "run", execute)
    backend = ComponentOCRBackend({"component": "ocr-paddle", "backend": "paddle"}, manager=manager)
    result = backend.recognize_rich(b"\x89PNG\r\n\x1a\nfixture")
    assert result.words[0]["bbox"] == [0, 0, 10, 10]
    assert result.metadata["original_mapping"] == "identity"


def test_production_ocr_factory_and_preflight_use_the_component_resolver(tmp_path, monkeypatch):
    from omnicrawler.core.config import load_config
    from omnicrawler.pdfx import ocr
    from omnicrawler.pipeline_ops.preflight import run_preflight

    manager, _key = _install(tmp_path)
    monkeypatch.setattr("omnicrawler.services.component_runtime.default_component_manager", lambda: manager)
    backend = ocr.create_backend(SimpleNamespace(ocr={"component": "ocr-paddle", "backend": "paddle"}))
    assert isinstance(backend, ComponentOCRBackend)
    ocr._ocr_worker_init({"component": "ocr-paddle", "backend": "paddle"})
    assert isinstance(ocr._worker_backend, ComponentOCRBackend)
    path = tmp_path / "task.yaml"
    path.write_text("project: {name: ocr, workspace: work}\nsource: {kind: static_html, seeds: [https://example.org/]}\nprocessors: {pdf: {enabled: true, ocr_backend: paddle, ocr_component: ocr-paddle}}\n", encoding="utf-8")
    report = run_preflight(load_config(path))
    assert next(item for item in report["checks"] if item["code"] == "ocr_component")["status"] == "ok"
    manager.uninstall("ocr-paddle")
    report = run_preflight(load_config(path))
    assert not report["ok"]
    assert next(item for item in report["checks"] if item["code"] == "ocr_component")["status"] == "error"
