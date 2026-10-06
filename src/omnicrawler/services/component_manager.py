from __future__ import annotations

import base64
import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from ..core.archive_security import (
    DEFAULT_ZIP_READ_LIMITS,
    copy_zip_member,
    read_zip_member,
    validate_zip_archive,
)
from ..core.utils import atomic_write, utcnow
from .component_compatibility import check_registry, dependency
from .component_registry import (
    commit_document,
    contained_path,
    read_document,
    recover_document,
    registry_lock,
    safe_identifier,
)


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
    core_version: str = ""
    platforms: tuple[str, ...] = ()
    architectures: tuple[str, ...] = ()
    runtime: dict[str, Any] = field(default_factory=dict)


class ComponentManager:
    def __init__(self, root: Path, *, trusted_public_key: bytes | None = None) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / "installed.json"
        self.trusted_public_key = trusted_public_key

    def list(self) -> list[dict[str, Any]]:
        with registry_lock(self.root):
            recover_document(self.root)
            return [self._public_entry(entry) for entry in self._installed().values()]

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
            if not isinstance(raw, dict):
                raise ValueError("组件清单格式无效")
            safe_identifier(str(raw.get("name", "")))
            safe_identifier(str(raw.get("version", "")))
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
                if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
                    raise ValueError("组件文件哈希格式无效")
                info = members.get(str(name))
                if info is None or info.is_dir():
                    raise ValueError(f"组件包缺少文件: {name}")
                if _hash_zip_member(archive, info) != expected:
                    raise ValueError(f"组件文件哈希不匹配: {name}")
            for key in ("dependencies", "platforms", "architectures"):
                if not isinstance(raw.get(key, []), list) or not all(isinstance(item, str) for item in raw.get(key, [])):
                    raise ValueError(f"组件{key}清单无效")
            for requirement in raw.get("dependencies", []):
                dependency(requirement)
            runtime = raw.get("runtime", {})
            if not isinstance(runtime, dict):
                raise ValueError("组件runtime清单无效")
            if runtime:
                if runtime.get("protocol") != "ocr-file-v1" or runtime.get("engine") not in {"paddle", "tesseract"}:
                    raise ValueError("不支持的组件运行协议")
                entrypoint = str(runtime.get("entrypoint", ""))
                _safe_component_path(entrypoint)
                if entrypoint not in files or not isinstance(runtime.get("model_license"), str) or not runtime["model_license"].strip():
                    raise ValueError("OCR组件必须声明已校验入口和模型许可")
            declared_disk = raw.get("disk_bytes", 0)
            if type(declared_disk) is not int or declared_disk < 0:
                raise ValueError("组件磁盘需求必须是非负整数")
            payload_disk = sum(members[name].file_size for name in files)
        return ComponentInfo(
            name=str(raw["name"]), version=str(raw["version"]), purpose=str(raw.get("purpose", "")),
            edition=str(raw.get("edition", "optional")), download_bytes=package.stat().st_size,
            disk_bytes=max(declared_disk, payload_disk), dependencies=tuple(str(item) for item in raw.get("dependencies", [])),
            uninstall_impact=str(raw.get("uninstall_impact", "依赖此组件的任务将无法运行")),
            files={str(key): str(value) for key, value in files.items()},
            core_version=str(raw.get("core_version", "")),
            platforms=tuple(raw.get("platforms", [])), architectures=tuple(raw.get("architectures", [])),
            runtime=runtime,
        )

    def registry_digest(self) -> str:
        with registry_lock(self.root):
            recover_document(self.root)
            return self._registry_digest_locked()

    def _registry_digest_locked(self) -> str:
        payload = json.dumps(self._installed(), sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(payload).hexdigest()

    def _require_reviewed_registry(self, expected: str) -> None:
        if expected and expected != self._registry_digest_locked():
            raise ValueError("组件状态已变化，请重新读取并确认")

    def import_offline(self, package: Path, *, allow_unsigned: bool = False,
                       expected_registry_sha256: str = "", expected_package_sha256: str = "") -> dict[str, Any]:
        with registry_lock(self.root):
            recover_document(self.root)
            self._require_reviewed_registry(expected_registry_sha256)
            return self._import_locked(package, allow_unsigned=allow_unsigned, expected_package_sha256=expected_package_sha256)

    def _import_locked(self, package: Path, *, allow_unsigned: bool, expected_package_sha256: str = "") -> dict[str, Any]:
        # Use the same private package bytes for signature verification and extraction.
        staging = contained_path(self.root, ".staging")
        staging.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=staging) as temporary:
            snapshot = Path(temporary) / "package.ocp"
            if shutil.disk_usage(self.root).free < package.stat().st_size:
                raise OSError("磁盘空间不足，无法暂存组件包")
            shutil.copyfile(package, snapshot)
            if expected_package_sha256 and _sha256(snapshot) != expected_package_sha256:
                raise ValueError("组件包已变化，请重新检查并确认")
            return self._install_snapshot(snapshot, allow_unsigned=allow_unsigned)

    def _install_snapshot(self, package: Path, *, allow_unsigned: bool) -> dict[str, Any]:
        info = self.inspect_package(package, allow_unsigned=allow_unsigned)
        installed = self._installed()
        if any(name.casefold() == info.name.casefold() and name != info.name for name in installed):
            raise ValueError("组件名称在不区分大小写的文件系统中冲突")
        proposed = {**installed, info.name: asdict(info)}
        check_registry(proposed)
        target = contained_path(self.root, f"{info.name}/{info.version}")
        if target.exists():
            if installed.get(info.name, {}).get("version") == info.version:
                raise FileExistsError(f"组件版本已安装: {info.name} {info.version}")
            # An interrupted activation or retained version is reusable only if
            # every byte and the complete file set match the newly verified package.
            actual_files = {path.relative_to(target).as_posix() for path in target.rglob("*") if path.is_file()}
            if actual_files != set(info.files):
                raise ValueError("保留组件目录与受信包文件集合不一致")
            self._verify_files({**asdict(info), "path": target.relative_to(self.root).as_posix()})
        else:
            if shutil.disk_usage(self.root).free < info.disk_bytes:
                raise OSError("磁盘空间不足，无法安装组件")
            payload_root = package.parent / "payload"
            payload_root.mkdir()
            with zipfile.ZipFile(package) as archive:
                members = validate_zip_archive(
                    archive, required=("component.json",), limits=DEFAULT_ZIP_READ_LIMITS
                )
                for name in info.files:
                    relative = _safe_component_path(name)
                    destination = contained_path(self.root, str(payload_root.joinpath(*relative.parts)))
                    member = members.get(name)
                    if member is None or member.is_dir():
                        raise ValueError(f"组件包缺少文件: {name}")
                    if copy_zip_member(archive, member, destination) != info.files[name]:
                        raise ValueError(f"组件文件哈希不匹配: {name}")
            target.parent.mkdir(exist_ok=True)
            payload_root.replace(target)
        # Preserve the original signature outside the payload file set. Runtime
        # lookup re-verifies it against the current trust root, not installed.json.
        with zipfile.ZipFile(package) as archive:
            manifest_bytes = archive.read("component.json")
        signed_manifest = contained_path(self.root, f".manifests/{info.name}/{info.version}.json")
        signed_manifest.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(signed_manifest, manifest_bytes)
        previous = {info.name: installed[info.name]} if info.name in installed else {}
        installed[info.name] = {**asdict(info), "dependencies": list(info.dependencies), "path": target.relative_to(self.root).as_posix(), "installed_at": utcnow()}
        self._save(installed, previous)
        return self._public_entry(installed[info.name])

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

    def uninstall(self, name: str, *, expected_registry_sha256: str = "") -> dict[str, Any]:
        safe_identifier(name)
        with registry_lock(self.root):
            recover_document(self.root)
            self._require_reviewed_registry(expected_registry_sha256)
            return self._uninstall_locked(name)

    def _uninstall_locked(self, name: str) -> dict[str, Any]:
        installed = self._installed()
        if name not in installed:
            raise KeyError(f"组件未安装: {name}")
        dependents = [item for item, value in installed.items() if any(dependency(raw)[0] == name for raw in value.get("dependencies", []))]
        if dependents:
            raise ValueError("以下组件仍依赖它: " + ", ".join(dependents))
        entry = installed.pop(name)
        path = self._entry_path(entry)
        self._save(installed, {name: entry})
        # Keep immutable bytes for rollback and tasks already using this version.
        return {"uninstalled": name, "recoverable_from": str(path), "impact": entry.get("uninstall_impact", "")}

    def rollback(self, name: str, *, expected_registry_sha256: str = "") -> dict[str, Any]:
        safe_identifier(name)
        with registry_lock(self.root):
            recover_document(self.root)
            self._require_reviewed_registry(expected_registry_sha256)
            return self._rollback_locked(name)

    def _rollback_locked(self, name: str) -> dict[str, Any]:
        rollback = contained_path(self.root, f".rollback/{name}.json")
        if not rollback.is_file():
            raise FileNotFoundError(f"组件没有可用回滚版本: {name}")
        installed = self._installed()
        entry = read_document(rollback)
        if entry.get("name") != name:
            raise ValueError("组件回滚名称与清单不一致")
        self._verify_files(entry)
        check_registry({**installed, name: entry})
        previous = {name: installed[name]} if name in installed else {}
        installed[name] = {**entry, "rolled_back_at": utcnow()}
        self._save(installed, previous)
        return self._public_entry(installed[name])

    def _verify_files(self, entry: dict[str, Any]) -> None:
        restored = self._entry_path(entry)
        for relative, expected in entry.get("files", {}).items():
            _safe_component_path(relative)
            file_path = contained_path(self.root, str(restored / relative))
            original_path = restored / relative
            linked = any(part.is_symlink() or part.is_junction() for part in (original_path, *original_path.parents) if part != self.root and self.root in part.parents)
            if linked or not file_path.is_file() or _sha256(file_path) != expected:
                raise ValueError("回滚组件文件缺失或哈希不匹配")

    def _installed(self) -> dict[str, dict[str, Any]]:
        value = read_document(self.manifest_path)
        for name, entry in value.items():
            safe_identifier(name)
            if not isinstance(entry, dict) or entry.get("name") != name:
                raise ValueError("组件注册表名称与清单不一致")
            self._entry_path(entry)
        return value

    def _save(self, value: dict[str, Any], previous: dict[str, Any] | None = None) -> None:
        for entry in value.values():
            entry["path"] = self._entry_path(entry).relative_to(self.root).as_posix()
        normalized = {name: {**entry, "path": self._entry_path(entry).relative_to(self.root).as_posix()}
                      for name, entry in (previous or {}).items()}
        commit_document(self.root, value, normalized)

    def _entry_path(self, entry: dict[str, Any]) -> Path:
        safe_identifier(str(entry["name"]))
        safe_identifier(str(entry["version"]))
        expected = contained_path(self.root, f"{entry['name']}/{entry['version']}")
        actual = contained_path(self.root, str(entry["path"]))
        if actual != expected:
            raise ValueError("组件目录与名称版本不一致")
        return actual

    def _public_entry(self, entry: dict[str, Any]) -> dict[str, Any]:
        return {**entry, "path": str(self._entry_path(entry))}

def _safe_component_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or "\\" in value or ":" in value:
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
