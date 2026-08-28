"""Safe inspection and atomic import for local/P2P/market package folders."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import yaml

from .package_manifest import (
    CREATOR_SIGNATURE_NAME,
    MAINTAINER_SIGNATURE_NAME,
    MANIFEST_NAME,
    verify_package,
)

PackageSource = Literal["local", "p2p", "market"]


class PackageImportError(PermissionError):
    """A package cannot be imported without weakening the trust boundary."""


@dataclass(frozen=True, slots=True)
class PackageInspection:
    package_type: str
    package_id: str
    version: str
    requested_username: str
    creator_fingerprint: str
    manifest_sha256: str
    permissions: tuple[str, ...]
    domains: tuple[str, ...]
    maintainer_signed: bool


def _declared_capabilities(package_dir: Path, package_type: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    source = package_dir / ("plugin.yaml" if package_type == "plugin" else "template.yaml")
    try:
        value = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise PackageImportError(f"无法静态读取权限清单: {exc}") from exc
    if not isinstance(value, dict):
        raise PackageImportError("权限清单必须是 YAML 映射")
    block = value if package_type == "plugin" else value.get("template", value)
    if not isinstance(block, dict):
        block = {}
    permissions = block.get("permissions") or []
    domains = block.get("domains") or []
    if not isinstance(permissions, list) or not all(isinstance(item, str) for item in permissions):
        raise PackageImportError("permissions 必须是字符串列表")
    if not isinstance(domains, list) or not all(isinstance(item, str) for item in domains):
        raise PackageImportError("domains 必须是字符串列表")
    return tuple(permissions), tuple(domains)


def inspect_package(
    package_dir: Path,
    *,
    source: PackageSource = "p2p",
    maintainer_public_key: bytes | None = None,
) -> PackageInspection:
    verified = verify_package(
        package_dir,
        maintainer_public_key=maintainer_public_key,
        require_maintainer=source == "market",
    )
    permissions, domains = _declared_capabilities(package_dir, verified.package_type)
    return PackageInspection(
        package_type=verified.package_type,
        package_id=verified.package_id,
        version=verified.version,
        requested_username=verified.creator.username,
        creator_fingerprint=verified.creator.key_fingerprint,
        manifest_sha256=verified.manifest_sha256,
        permissions=permissions,
        domains=domains,
        maintainer_signed=verified.maintainer_signed,
    )


def import_package_folder(
    package_dir: Path,
    destination_root: Path,
    *,
    source: PackageSource,
    approved_permissions: set[str],
    maintainer_public_key: bytes | None = None,
) -> tuple[Path, PackageInspection]:
    """Copy verified bytes atomically after explicit permission approval.

    Trusting a creator is intentionally outside this function.  Callers must
    make a separate fingerprint-scoped trust decision before invoking it.
    """
    package_dir = package_dir.resolve()
    inspection = inspect_package(
        package_dir,
        source=source,
        maintainer_public_key=maintainer_public_key,
    )
    undeclared_approval = approved_permissions - set(inspection.permissions)
    if undeclared_approval:
        raise PackageImportError(f"批准列表包含插件未声明的权限: {sorted(undeclared_approval)}")
    missing_approval = set(inspection.permissions) - approved_permissions
    if missing_approval:
        raise PackageImportError(f"以下权限尚未由用户明确批准: {sorted(missing_approval)}")
    destination_root = destination_root.resolve()
    destination_root.mkdir(parents=True, exist_ok=True)
    final = (
        destination_root
        / inspection.creator_fingerprint
        / Path(*inspection.package_id.split("/"))
        / inspection.version
    )
    if final.exists():
        raise PackageImportError(f"相同作者、ID、版本的插件已存在: {final}")
    manifest = json.loads((package_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    generated = [MANIFEST_NAME, CREATOR_SIGNATURE_NAME, "creator.sig"]
    if (package_dir / MAINTAINER_SIGNATURE_NAME).is_file():
        generated.append(MAINTAINER_SIGNATURE_NAME)
    with tempfile.TemporaryDirectory(prefix="omnicrawl-import-", dir=destination_root) as temp:
        staging = Path(temp) / "package"
        staging.mkdir()
        for rel in sorted(manifest["files"]):
            source_path = package_dir / Path(*str(rel).split("/"))
            target = staging / Path(*str(rel).split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_path, target)
        for name in generated:
            source_path = package_dir / name
            if source_path.is_file():
                shutil.copyfile(source_path, staging / name)
        # Close the verify/copy race: the bytes in staging are independently
        # verified before the atomic move, rather than trusting source paths.
        inspect_package(
            staging,
            source=source,
            maintainer_public_key=maintainer_public_key,
        )
        final.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, final)
    provenance_dir = destination_root / ".provenance"
    provenance_dir.mkdir(exist_ok=True)
    provenance = {
        "schema_version": 1,
        "source": source,
        "package_type": inspection.package_type,
        "package_id": inspection.package_id,
        "version": inspection.version,
        "creator_fingerprint": inspection.creator_fingerprint,
        "package_manifest_sha256": inspection.manifest_sha256,
        "approved_permissions": sorted(approved_permissions),
        "imported_at": datetime.now(UTC).isoformat(),
    }
    (provenance_dir / f"{inspection.manifest_sha256}.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return final, inspection
