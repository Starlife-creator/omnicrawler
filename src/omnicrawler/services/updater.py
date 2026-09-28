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

#: 升级包**永远不许触碰**的顶层路径（越界即整包拒绝）。
#:
#: 判据是"合法升级**绝不**需要写它"：
#: * `work` / `data` / `output` / `logs` / `.omnicrawler`：用户数据与密钥；
#: * `plugins_installed`：**市场安装的插件**（构建脚本从不产出它，纯运行期数据，
#:   合法升级包没有任何理由写它）—— 此前漏在名单外，升级包可以覆盖用户已装插件；
#: * `PORTABLE.flag` / `portable.flag`：便携模式标记（写错会改掉数据根判定）。
#:
#: ★ **`configs/` 刻意不在名单里**：它是**随包目录**（`configs/plugin_trust.pub.pem`
#: 是内置信任根，由构建脚本 `build_*.ps1/sh` 随包复制），整目录保护会让
#: "升级时轮换信任根"永远做不到。故对 `configs/` 的约束是"**只允许随包文件被更新**"，
#: 而不是"禁止触碰" —— 不要为了"看起来更安全"把它加进来。
PROTECTED_TOP_LEVEL = {
    "work",
    "data",
    "output",
    "logs",
    ".omnicrawler",
    "plugins_installed",
    "PORTABLE.flag",
    "portable.flag",
}


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
