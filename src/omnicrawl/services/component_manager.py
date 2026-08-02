from __future__ import annotations

import base64
import hashlib
import json
import shutil
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..core.archive_security import (
    DEFAULT_ZIP_READ_LIMITS,
    copy_zip_member,
    read_zip_member,
    validate_zip_archive,
)
from ..core.utils import atomic_write, utcnow


@dataclass(frozen=True, slots=True)
class ComponentInfo:
    name: str
    version: str
    purpose: str
    edition: str
    download_bytes: int
    disk_bytes: int
    dependencies: tuple[str, ...]
    uninstall_impact: str
    files: dict[str, str]


class ComponentManager:
    def __init__(self, root: Path, *, trusted_public_key: bytes | None = None) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "installed.json"
        self.trusted_public_key = trusted_public_key

    def list(self) -> list[dict[str, Any]]:
        return list(self._installed().values())

    def inspect_package(self, package: Path, *, allow_unsigned: bool = False) -> ComponentInfo:
        package = package.resolve()
        with zipfile.ZipFile(package) as archive:
            members = validate_zip_archive(
                archive, required=("component.json",), limits=DEFAULT_ZIP_READ_LIMITS
            )
            manifest_bytes = read_zip_member(
                archive, members["component.json"], maximum_bytes=DEFAULT_ZIP_READ_LIMITS.max_manifest_bytes
            )
            raw = json.loads(manifest_bytes)
            signature = raw.pop("signature", "")
            canonical = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            if self.trusted_public_key:
                _verify_ed25519(self.trusted_public_key, canonical, str(signature))
            elif not allow_unsigned:
                raise ValueError("组件包没有可用的受信签名密钥")
            files = raw.get("files", {})
            if not isinstance(files, dict):
                raise ValueError("组件files清单无效")
            for name, expected in files.items():
                _safe_component_path(str(name))
                info = members.get(str(name))
                if info is None or info.is_dir():
                    raise ValueError(f"组件包缺少文件: {name}")
                if _hash_zip_member(archive, info) != expected:
                    raise ValueError(f"组件文件哈希不匹配: {name}")
        return ComponentInfo(
            name=str(raw["name"]), version=str(raw["version"]), purpose=str(raw.get("purpose", "")),
            edition=str(raw.get("edition", "optional")), download_bytes=package.stat().st_size,
            disk_bytes=int(raw.get("disk_bytes", 0)), dependencies=tuple(str(item) for item in raw.get("dependencies", [])),
            uninstall_impact=str(raw.get("uninstall_impact", "依赖此组件的任务将无法运行")),
            files={str(key): str(value) for key, value in files.items()},
        )

    def import_offline(self, package: Path, *, allow_unsigned: bool = False) -> dict[str, Any]:
        info = self.inspect_package(package, allow_unsigned=allow_unsigned)
        installed = self._installed()
        missing = [name for name in info.dependencies if name not in installed]
        if missing:
            raise ValueError("缺少组件依赖: " + ", ".join(missing))
        target = self.root / info.name / info.version
        rollback = self.root / ".rollback" / info.name
        if target.exists():
            raise FileExistsError(f"组件版本已安装: {info.name} {info.version}")
        target.mkdir(parents=True)
        try:
            with zipfile.ZipFile(package) as archive:
                members = validate_zip_archive(
                    archive, required=("component.json",), limits=DEFAULT_ZIP_READ_LIMITS
                )
                for name in info.files:
                    relative = _safe_component_path(name)
                    destination = target.joinpath(*relative.parts)
                    member = members.get(name)
                    if member is None or member.is_dir():
                        raise ValueError(f"组件包缺少文件: {name}")
                    if copy_zip_member(archive, member, destination) != info.files[name]:
                        raise ValueError(f"组件文件哈希不匹配: {name}")
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
        if info.name in installed:
            previous = Path(str(installed[info.name]["path"]))
            rollback.parent.mkdir(parents=True, exist_ok=True)
            if rollback.exists():
                shutil.rmtree(rollback)
            shutil.copytree(previous, rollback)
        installed[info.name] = {**asdict(info), "dependencies": list(info.dependencies), "path": str(target), "installed_at": utcnow()}
        self._save(installed)
        return installed[info.name]

    def stage_resumable(self, source: Path, expected_sha256: str, *, chunk_size: int = 1024 * 1024) -> dict[str, Any]:
        """Resume a previously interrupted package copy into the managed download area."""

        source = source.resolve()
        downloads = self.root / ".downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        partial = downloads / f"{source.name}.partial"
        completed = downloads / source.name
        offset = partial.stat().st_size if partial.is_file() else 0
        if offset > source.stat().st_size:
            partial.unlink()
            offset = 0
        with source.open("rb") as reader, partial.open("ab") as writer:
            reader.seek(offset)
            for block in iter(lambda: reader.read(chunk_size), b""):
                writer.write(block)
        if _sha256(partial) != expected_sha256.casefold():
            raise ValueError("组件暂存完成但SHA-256不匹配")
        partial.replace(completed)
        return {"package": str(completed), "resumed_from": offset, "bytes": completed.stat().st_size}

    def uninstall(self, name: str) -> dict[str, Any]:
        installed = self._installed()
        if name not in installed:
            raise KeyError(f"组件未安装: {name}")
        dependents = [item for item, value in installed.items() if name in value.get("dependencies", [])]
        if dependents:
            raise ValueError("以下组件仍依赖它: " + ", ".join(dependents))
        entry = installed.pop(name)
        path = Path(str(entry["path"])).resolve()
        if self.root not in path.parents:
            raise ValueError("组件路径越出组件根目录")
        rollback = self.root / ".rollback" / name
        rollback.parent.mkdir(parents=True, exist_ok=True)
        if rollback.exists():
            shutil.rmtree(rollback)
        shutil.move(str(path), str(rollback))
        self._save(installed)
        return {"uninstalled": name, "recoverable_from": str(rollback), "impact": entry.get("uninstall_impact", "")}

    def rollback(self, name: str) -> dict[str, Any]:
        rollback = self.root / ".rollback" / name
        if not rollback.is_dir():
            raise FileNotFoundError(f"组件没有可用回滚版本: {name}")
        installed = self._installed()
        current = Path(str(installed.get(name, {}).get("path", "")))
        if current.is_dir() and self.root in current.resolve().parents:
            shutil.rmtree(current)
        restored = self.root / name / f"rollback-{utcnow().replace(':', '-')}"
        restored.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(rollback), str(restored))
        installed[name] = {**installed.get(name, {}), "path": str(restored), "rolled_back_at": utcnow()}
        self._save(installed)
        return installed[name]

    def _installed(self) -> dict[str, dict[str, Any]]:
        try:
            value = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _save(self, value: dict[str, Any]) -> None:
        atomic_write(self.manifest_path, json.dumps(value, ensure_ascii=False, indent=2).encode())


def _safe_component_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError(f"不安全的组件路径: {value}")
    return path


def _verify_ed25519(public_key: bytes, payload: bytes, signature: str) -> None:
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise RuntimeError("验证组件签名需要cryptography") from exc
    Ed25519PublicKey.from_public_bytes(public_key).verify(base64.b64decode(signature), payload)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _hash_zip_member(archive: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    digest = hashlib.sha256()
    with archive.open(info) as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
