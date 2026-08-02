from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any

from .utils import atomic_write, utcnow

RUNTIME_MANIFEST = "RUNTIME-MANIFEST.json"


def create_runtime_manifest(root: Path, *, include: Iterable[Path] | None = None) -> dict[str, Any]:
    root = root.resolve()
    paths = include if include is not None else (path for path in root.rglob("*") if path.is_file())
    files: dict[str, dict[str, Any]] = {}
    for path in sorted((Path(item).resolve() for item in paths), key=str):
        if path.name == RUNTIME_MANIFEST or root not in path.parents:
            continue
        relative = path.relative_to(root).as_posix()
        files[relative] = {"sha256": _sha256(path), "bytes": path.stat().st_size}
    manifest = {"format": 1, "created_at": utcnow(), "files": files}
    atomic_write(root / RUNTIME_MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2).encode())
    return manifest


def verify_runtime_manifest(root: Path) -> dict[str, Any]:
    root = root.resolve()
    path = root / RUNTIME_MANIFEST
    if not path.is_file():
        return {"ok": False, "status": "missing_manifest", "missing": [], "corrupt": []}
    value = json.loads(path.read_text(encoding="utf-8"))
    files = value.get("files", {}) if isinstance(value, dict) else {}
    missing: list[str] = []
    corrupt: list[str] = []
    for name, expected in files.items():
        relative = PurePosixPath(str(name))
        if relative.is_absolute() or ".." in relative.parts:
            corrupt.append(str(name))
            continue
        target = root.joinpath(*relative.parts)
        if not target.is_file():
            missing.append(str(name))
        elif target.stat().st_size != int(expected["bytes"]) or _sha256(target) != expected["sha256"]:
            corrupt.append(str(name))
    return {"ok": not missing and not corrupt, "status": "valid" if not missing and not corrupt else "invalid", "missing": missing, "corrupt": corrupt, "checked": len(files)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
