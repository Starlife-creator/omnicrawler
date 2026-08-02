import base64
import hashlib
import json
import zipfile
from pathlib import Path

from tools.check_release_integrity import (
    check_portable_zip,
    check_project,
    check_source_zip,
    check_wheel,
)


def test_release_integrity_has_no_broken_local_imports_or_entry_points():
    project_root = Path(__file__).resolve().parents[3]
    assert check_project(project_root) == []


def _write_wheel(path: Path, files: dict[str, bytes]) -> None:
    record = []
    for name, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        record.append(f"{name},sha256={digest},{len(content)}")
    record.append("demo-1.0.dist-info/RECORD,,")
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr("demo-1.0.dist-info/RECORD", "\n".join(record))


def test_wheel_integrity_checks_record_imports_and_entry_points(tmp_path):
    wheel = tmp_path / "demo.whl"
    _write_wheel(wheel, {
        "omnicrawl/__init__.py": b"",
        "omnicrawl/quality/__init__.py": b"",
        "omnicrawl/quality/diagnostics.py": b"class DiagnosticRecorder:\n    pass\n",
        "omnicrawl/pipeline.py": b"from .quality.diagnostics import DiagnosticRecorder\ndef main():\n    pass\n",
        "demo-1.0.dist-info/entry_points.txt": b"[console_scripts]\ndemo=omnicrawl.pipeline:main\n",
    })
    assert check_wheel(wheel) == []


def test_wheel_integrity_detects_missing_imported_symbol(tmp_path):
    wheel = tmp_path / "broken.whl"
    _write_wheel(wheel, {
        "omnicrawl/__init__.py": b"",
        "omnicrawl/quality/__init__.py": b"",
        "omnicrawl/quality/diagnostics.py": b"",
        "omnicrawl/pipeline.py": b"from .quality.diagnostics import DiagnosticRecorder\n",
    })
    assert any("has no DiagnosticRecorder" in issue for issue in check_wheel(wheel))


def test_source_zip_checks_paths_generated_files_and_imported_symbols(tmp_path):
    source_zip = tmp_path / "source.zip"
    files = {
        "Demo/pyproject.toml": "[project]\nname='demo'\nversion='1.0'\n",
        "Demo/README.md": "demo",
        "Demo/LICENSE": "MIT",
        "Demo/tools/check_release_integrity.py": "",
        "Demo/src/omnicrawl/__init__.py": "",
        "Demo/src/omnicrawl/quality/__init__.py": "",
        "Demo/src/omnicrawl/quality/diagnostics.py": "class DiagnosticRecorder:\n    pass\n",
        "Demo/src/omnicrawl/pipeline.py": "from .quality.diagnostics import DiagnosticRecorder\n",
    }
    with zipfile.ZipFile(source_zip, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    assert check_source_zip(source_zip) == []

    broken_zip = tmp_path / "broken-source.zip"
    with zipfile.ZipFile(broken_zip, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
        archive.writestr("Demo/coverage.json", "{}")
        archive.writestr("../escape.txt", "unsafe")
    issues = check_source_zip(broken_zip)
    assert any("generated file" in issue for issue in issues)
    assert any("unsafe archive path" in issue for issue in issues)


def _write_portable(
    path: Path,
    edition: str,
    *,
    corrupt_hash: bool = False,
    unverified_models: bool = False,
) -> None:
    files: dict[str, bytes] = {
        "OmniCrawler.exe": b"MZ-gui",
        "omnicrawl.exe": b"MZ-cli",
        "omnicrawl-worker.exe": b"MZ-worker",
        "_internal/python312.dll": b"runtime",
        "PORTABLE.flag": b"",
        "EDITION.txt": f"OmniCrawler {edition} portable edition\n".encode(),
        "CAPABILITIES.json": b"{}",
        "SBOM.json": b"{}",
        "THIRD_PARTY_NOTICES.md": b"notices",
        "browsers/chromium-1234/chrome-win64/chrome.exe": b"MZ-chromium",
    }
    if edition == "Full":
        model = "PP-OCRv5_server_rec"
        files.update({
            "runtime/selenium/chromedriver.exe": b"MZ-driver",
            "runtime/tesseract/tesseract.exe": b"MZ-tesseract",
            "runtime/tesseract/tessdata/eng.traineddata": b"eng",
            "runtime/tesseract/tessdata/chi_sim.traineddata": b"chi",
            "runtime/tesseract/tessdata/osd.traineddata": b"osd",
            "runtime/models/paddlex/omnicrawler-model-manifest.json": json.dumps({
                "verified": not unverified_models,
                "models": [model],
            }).encode(),
            f"runtime/models/paddlex/official_models/{model}/inference.pdiparams": b"model",
        })
    records = {}
    for name, content in files.items():
        digest = hashlib.sha256(content).hexdigest()
        if corrupt_hash and name == "omnicrawl.exe":
            digest = "0" * 64
        records[name] = {"sha256": digest, "bytes": len(content)}
    files["RUNTIME-MANIFEST.json"] = json.dumps({
        "format": 1,
        "created_at": "2026-07-25T00:00:00+00:00",
        "files": records,
    }).encode()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(f"OmniCrawler/{name}", content)


def test_portable_zip_accepts_standard_and_full_editions(tmp_path):
    for edition in ("Standard", "Full"):
        archive = tmp_path / f"OmniCrawler-2.1.0-Windows-Portable-{edition}.zip"
        _write_portable(archive, edition)
        assert check_portable_zip(archive, verify_payloads=True) == []


def test_portable_zip_detects_unsafe_duplicates_and_manifest_hashes(tmp_path):
    archive = tmp_path / "OmniCrawler-2.1.0-Windows-Portable-Standard.zip"
    _write_portable(archive, "Standard", corrupt_hash=True)
    with zipfile.ZipFile(archive, "a") as bundle:
        bundle.writestr("OmniCrawler/OMNICRAWL.exe", b"MZ-duplicate")
        bundle.writestr("../escape.txt", b"unsafe")
    issues = check_portable_zip(archive, verify_payloads=True)
    assert any("duplicate portable archive path" in issue for issue in issues)
    assert any("unsafe portable archive path" in issue for issue in issues)
    assert any("runtime manifest hash mismatch: omnicrawl.exe" in issue for issue in issues)


def test_portable_zip_requires_full_runtime_assets(tmp_path):
    archive = tmp_path / "OmniCrawler-2.1.0-Windows-Portable-Full.zip"
    _write_portable(archive, "Standard")
    issues = check_portable_zip(archive)
    assert any("Full portable archive missing runtime asset" in issue for issue in issues)
    assert any("declares Standard, expected Full" in issue for issue in issues)


def test_portable_zip_requires_verified_paddle_model_manifest(tmp_path):
    archive = tmp_path / "OmniCrawler-2.1.0-Windows-Portable-Full.zip"
    _write_portable(archive, "Full", unverified_models=True)
    assert any("not marked verified" in issue for issue in check_portable_zip(archive))
