from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from ..security.paths import require_workspace_path
from ..state import StateStore


def verify_artifacts(
    state: StateStore,
    run_id: str | None = None,
    *,
    workspace: Path | None = None,
) -> dict[str, Any]:
    """B06-006：校验 artifacts.local_path 必须在工作区内（DB 中路径可能被外部写入）。"""
    root = workspace if workspace is not None else state.path.parent
    where, params = (" WHERE run_id=?", (run_id,)) if run_id else ("", ())
    rows = state.rows(f"SELECT local_path, size_bytes, sha256, source_url FROM artifacts{where}", params)
    results: list[dict[str, Any]] = []
    valid = missing = corrupt = 0
    for row in rows:
        path = require_workspace_path(
            str(row["local_path"]), root=root, what="artifact local_path"
        )
        status = "valid"
        actual_hash = ""
        if not path.is_file():
            status = "missing"
            missing += 1
        else:
            actual_hash = _sha256(path)
            if path.stat().st_size != int(row["size_bytes"]) or actual_hash != row["sha256"]:
                status = "corrupt"
                corrupt += 1
            else:
                valid += 1
        results.append(
            {
                "source_url": row["source_url"],
                "path": str(path),
                "status": status,
                "expected_sha256": row["sha256"],
                "actual_sha256": actual_hash,
            }
        )
    return {
        "ok": missing == 0 and corrupt == 0,
        "total": len(rows),
        "valid": valid,
        "missing": missing,
        "corrupt": corrupt,
        "items": results,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
