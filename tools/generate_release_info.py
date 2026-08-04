"""Generate a small, machine-readable description for a portable release.

The file intentionally records only facts observable in the staged release.  It
does not claim that optional models or browser engines are usable merely because
their directories exist; the packaged ``capabilities`` command remains the
runtime authority for that check.
"""

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path
from typing import Any

RELEASE_INFO = "RELEASE-INFO.json"
RUNTIME_MANIFEST = "RUNTIME-MANIFEST.json"
BASE_REQUIRED = ("OmniCrawler.exe", "omnicrawl.exe", "omnicrawl-worker.exe", "_internal")
FULL_REQUIRED = (
    "runtime/selenium/chromedriver.exe",
    "runtime/tesseract/tesseract.exe",
    "runtime/models/paddlex/omnicrawler-model-manifest.json",
)


def build_release_info(project_root: Path, release_root: Path, edition: str) -> dict[str, Any]:
    """Describe the staged release without importing application dependencies."""
    edition = edition.strip().title()
    if edition not in {"Standard", "Full"}:
        raise ValueError("edition must be Standard or Full")
    metadata = tomllib.loads((project_root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    files = [path for path in release_root.rglob("*") if path.is_file() and path.name != RELEASE_INFO]
    required = [*BASE_REQUIRED, *(FULL_REQUIRED if edition == "Full" else [])]
    missing = [name for name in required if not (release_root / name).exists()]
    manifest_path = release_root / RUNTIME_MANIFEST
    manifest_files = 0
    if manifest_path.is_file():
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        files_payload = payload.get("files", {}) if isinstance(payload, dict) else {}
        manifest_files = len(files_payload) if isinstance(files_payload, dict) else 0
    return {
        "format": 1,
        "project": {
            "name": str(metadata["name"]),
            "version": str(metadata["version"]),
            "requires_python": str(metadata["requires-python"]),
        },
        "edition": edition,
        "release_root": release_root.name,  # F42：只保留包根目录名，不泄漏构建机绝对路径
        "artifacts": {"file_count": len(files), "bytes": sum(path.stat().st_size for path in files)},
        "runtime_manifest": {
            "name": RUNTIME_MANIFEST,
            "present": manifest_path.is_file(),
            "files": manifest_files,
        },
        "required_components": {"present": not missing, "missing": missing},
        "status": "ready_for_capability_check" if manifest_path.is_file() and not missing else "incomplete",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate portable release metadata")
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--release-root", type=Path, required=True)
    parser.add_argument("--edition", choices=("Standard", "Full"), required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    info = build_release_info(args.project_root.resolve(), args.release_root.resolve(), args.edition)
    output = args.output.resolve() if args.output else args.release_root.resolve() / RELEASE_INFO
    output.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
