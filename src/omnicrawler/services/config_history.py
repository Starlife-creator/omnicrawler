from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from typing import Any

from ..core.utils import atomic_write, utcnow


def _version_key(path: Path) -> tuple[int, str]:
    """Order new snapshots by their embedded nanosecond clock and old ones by mtime."""
    parts = path.stem.rsplit("_", 2)
    if len(parts) == 3 and parts[1].isdigit():
        return int(parts[1]), path.name
    return path.stat().st_mtime_ns, path.name


class ConfigHistory:
    def __init__(self, root: Path, *, keep: int = 50) -> None:
        self.root = root
        self.keep = max(2, keep)

    def snapshot(self, config_path: Path, *, reason: str = "manual_save") -> Path | None:
        if not config_path.is_file():
            return None
        payload = config_path.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        directory = self.root / config_path.stem
        directory.mkdir(parents=True, exist_ok=True)
        existing = sorted(directory.glob("*.yaml"), key=_version_key)
        if existing and hashlib.sha256(existing[-1].read_bytes()).hexdigest() == digest:
            return existing[-1]
        stamp = utcnow().replace(":", "-").replace("+", "_")
        target = directory / f"{stamp}_{time.time_ns():020d}_{digest[:10]}.yaml"
        shutil.copy2(config_path, target)
        meta = {"created_at": utcnow(), "source": str(config_path), "reason": reason, "sha256": digest}
        atomic_write(target.with_suffix(".json"), json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8"))
        versions = sorted(directory.glob("*.yaml"), key=_version_key)
        for old in versions[:-self.keep]:
            old.unlink(missing_ok=True)
            old.with_suffix(".json").unlink(missing_ok=True)
        return target

    def list(self, name: str) -> list[dict[str, Any]]:
        directory = self.root / Path(name).stem
        result: list[dict[str, Any]] = []
        for path in sorted(directory.glob("*.yaml"), key=_version_key, reverse=True):
            meta_path = path.with_suffix(".json")
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {"created_at": "", "reason": "unknown"}
            result.append({**meta, "path": str(path), "size_bytes": path.stat().st_size})
        return result

    def restore(self, version: Path, destination: Path) -> Path:
        version = version.resolve()
        root = self.root.resolve()
        if root not in version.parents or not version.is_file():
            raise ValueError("Config history version is outside the history directory")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(version, destination)
        return destination
