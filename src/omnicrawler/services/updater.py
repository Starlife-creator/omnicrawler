from __future__ import annotations

import json
import shutil
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from ..core.archive_security import (
    DEFAULT_ZIP_READ_LIMITS,
    copy_zip_member,
    read_zip_member,
    validate_zip_archive,
)
from .component_manager import _verify_ed25519

PROTECTED_TOP_LEVEL = {"work", "data", "output", "logs", ".omnicrawler", "PORTABLE.flag", "portable.flag"}


class UpgradeManager:
    def __init__(self, app_root: Path, *, trusted_public_key: bytes) -> None:
        self.app_root = app_root.resolve()
        self.trusted_public_key = trusted_public_key
        self.updates = self.app_root / ".updates"

    def stage(self, package: Path) -> dict[str, Any]:
        with zipfile.ZipFile(package) as archive:
            members = validate_zip_archive(
                archive, required=("upgrade.json",), limits=DEFAULT_ZIP_READ_LIMITS
            )
            raw = json.loads(
                read_zip_member(
                    archive, members["upgrade.json"], maximum_bytes=DEFAULT_ZIP_READ_LIMITS.max_manifest_bytes
                )
            )
            signature = str(raw.pop("signature", ""))
            canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            _verify_ed25519(self.trusted_public_key, canonical, signature)
            files = raw.get("files", {})
            stage = self.updates / "staging" / f"{raw.get('version', 'unknown')}-{time.time_ns()}"
            stage.mkdir(parents=True, exist_ok=False)
            for name, expected in files.items():
                relative = _safe_upgrade_path(str(name))
                member = members.get(str(name))
                if member is None or member.is_dir():
                    raise ValueError(f"升级包缺少文件: {name}")
                target = stage.joinpath(*relative.parts)
                if copy_zip_member(archive, member, target) != expected:
                    raise ValueError(f"升级文件哈希不匹配: {name}")
        return {"stage": str(stage), "version": raw.get("version"), "files": len(files)}

    def apply(self, stage: Path) -> dict[str, Any]:
        stage = stage.resolve()
        staging_root = (self.updates / "staging").resolve()
        if staging_root not in stage.parents:
            raise ValueError("升级暂存目录无效")
        rollback = self.updates / "rollback" / str(time.time_ns())
        applied: list[Path] = []
        try:
            for source in sorted(path for path in stage.rglob("*") if path.is_file()):
                relative = source.relative_to(stage)
                _safe_upgrade_path(relative.as_posix())
                destination = self.app_root / relative
                if destination.is_file():
                    backup = rollback / relative
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(destination, backup)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
                applied.append(relative)
        except Exception:
            for relative in reversed(applied):
                backup = rollback / relative
                destination = self.app_root / relative
                if backup.is_file():
                    shutil.copy2(backup, destination)
                else:
                    destination.unlink(missing_ok=True)
            raise
        return {"applied": len(applied), "rollback": str(rollback), "workspace_preserved": True}


def _safe_upgrade_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] in PROTECTED_TOP_LEVEL:
        raise ValueError(f"升级包包含受保护或不安全路径: {value}")
    return path
