"""Bounded-memory receipts for default exports inside their output directory."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..security.paths import require_workspace_path


def validate_default_export_directory(workspace: Path) -> Path:
    root = require_workspace_path(workspace.resolve() / "output", root=workspace,
                                  what="default export directory")
    if root.is_dir():
        for entry in root.iterdir():
            require_workspace_path(entry, root=root, what="default export destination")
    return root


def capture_export_receipts(workspace: Path, result: dict[str, Any]) -> dict[str, Any]:
    files = result.get("files", {})
    if not isinstance(files, dict):
        raise TypeError("Default export files must be a mapping")
    root = validate_default_export_directory(workspace)
    receipts: dict[str, Any] = {}
    for value in files.values():
        if not isinstance(value, str):
            raise TypeError("Default export file path must be a string")
        path = require_workspace_path(value, root=root, what="default export file")
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
                size += len(block)
        receipts[path.relative_to(root).as_posix()] = {
            "sha256": digest.hexdigest(), "size": size,
        }
    return receipts


def export_receipts_match(workspace: Path, result: dict[str, Any], receipts: object) -> bool:
    if not isinstance(receipts, dict):
        return False
    try:
        return capture_export_receipts(workspace, result) == receipts
    except (OSError, ValueError, TypeError):
        return False
