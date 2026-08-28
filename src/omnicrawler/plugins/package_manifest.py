"""Signed, distribution-neutral package manifests for plugins and templates.

The creator signs the canonical manifest, which in turn hashes every payload
file.  A market maintainer can countersign the *same bytes* without rewriting
the creator package.  The format is deliberately usable for both direct/P2P
sharing and curated-market distribution.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from .identity import CreatorIdentity, UserIdentity

PACKAGE_SCHEMA_VERSION = 1
MANIFEST_NAME = "package.manifest.json"
CREATOR_SIGNATURE_NAME = "package.manifest.creator.sig"
MAINTAINER_SIGNATURE_NAME = "package.manifest.maintainer.sig"
PackageType = Literal["plugin", "template"]

_GENERATED_NAMES = {
    MANIFEST_NAME,
    CREATOR_SIGNATURE_NAME,
    MAINTAINER_SIGNATURE_NAME,
    # Legacy detached signatures remain distributable compatibility artefacts,
    # but are not payload and therefore do not participate in the package hash.
    "creator.sig",
    "plugin.py.sig",
    "template.yaml.sig",
}
_IGNORED_PARTS = {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__"}
_FORBIDDEN_NAMES = {".env", "id_rsa", "id_ed25519"}
_FORBIDDEN_SUFFIXES = {".key", ".p12", ".pfx"}


class PackageManifestError(ValueError):
    """The package cannot be safely created or verified."""


@dataclass(frozen=True, slots=True)
class PackageVerification:
    package_type: PackageType
    package_id: str
    version: str
    creator: CreatorIdentity
    manifest_sha256: str
    maintainer_signed: bool


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """Return the only byte representation accepted for signatures."""
    return (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
    ):
        raise PackageManifestError(f"插件包包含非法相对路径: {value!r}")
    return path


def _payload_files(package_dir: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}
    for path in sorted(package_dir.rglob("*")):
        relative = path.relative_to(package_dir)
        if any(part in _IGNORED_PARTS for part in relative.parts):
            continue
        if path.is_symlink():
            raise PackageManifestError(f"插件包不允许符号链接: {relative}")
        if not path.is_file() or path.name in _GENERATED_NAMES:
            continue
        if path.name.lower() in _FORBIDDEN_NAMES or path.suffix.lower() in _FORBIDDEN_SUFFIXES:
            raise PackageManifestError(f"插件包疑似包含私钥或凭据文件: {relative}")
        rel = relative.as_posix()
        _safe_relative_path(rel)
        files[rel] = path
    return files


def build_manifest(
    package_dir: Path,
    *,
    package_type: PackageType,
    package_id: str,
    version: str,
    creator: CreatorIdentity,
) -> dict[str, Any]:
    package_dir = package_dir.resolve()
    if package_type not in ("plugin", "template"):
        raise PackageManifestError(f"不支持的包类型: {package_type!r}")
    if not package_id.strip() or not version.strip():
        raise PackageManifestError("package_id 与 version 不能为空")
    payload = _payload_files(package_dir)
    required = "plugin.py" if package_type == "plugin" else "template.yaml"
    if required not in payload:
        raise PackageManifestError(f"{package_type} 包缺少 {required}")
    if "creator.identity" not in payload:
        raise PackageManifestError("包缺少 creator.identity")
    return {
        "schema_version": PACKAGE_SCHEMA_VERSION,
        "package_type": package_type,
        "package_id": package_id,
        "version": version,
        "creator_fingerprint": creator.key_fingerprint,
        "requested_username": creator.username,
        "files": {
            rel: f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
            for rel, path in sorted(payload.items())
        },
    }


def sign_creator_package(
    package_dir: Path,
    *,
    package_type: PackageType,
    package_id: str,
    version: str,
    identity: UserIdentity,
    legacy_target: str | None = None,
) -> PackageVerification:
    """Finalize a creator-shareable folder and retain the legacy signature."""
    package_dir = package_dir.resolve()
    package_dir.mkdir(parents=True, exist_ok=True)
    creator = identity.export_identity()
    (package_dir / "creator.identity").write_text(
        json.dumps(creator.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    manifest = build_manifest(
        package_dir,
        package_type=package_type,
        package_id=package_id,
        version=version,
        creator=creator,
    )
    data = canonical_manifest_bytes(manifest)
    (package_dir / MANIFEST_NAME).write_bytes(data)
    (package_dir / CREATOR_SIGNATURE_NAME).write_bytes(identity.sign_bytes(data))
    if legacy_target:
        target = package_dir / legacy_target
        if not target.is_file():
            raise PackageManifestError(f"缺少兼容签名目标: {legacy_target}")
        (package_dir / "creator.sig").write_bytes(identity.sign_bytes(target.read_bytes()))
    return PackageVerification(
        package_type=package_type,
        package_id=package_id,
        version=version,
        creator=creator,
        manifest_sha256=hashlib.sha256(data).hexdigest(),
        maintainer_signed=False,
    )


def _verify_ed25519(public_key: bytes, signature: bytes, data: bytes, label: str) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, data)
    except (InvalidSignature, ValueError, TypeError) as exc:
        raise PackageManifestError(f"{label}签名校验失败") from exc


def verify_package(
    package_dir: Path,
    *,
    maintainer_public_key: bytes | None = None,
    require_maintainer: bool = False,
) -> PackageVerification:
    """Verify canonical manifest, exact payload set, hashes, and signatures."""
    package_dir = package_dir.resolve()
    manifest_path = package_dir / MANIFEST_NAME
    creator_sig_path = package_dir / CREATOR_SIGNATURE_NAME
    identity_path = package_dir / "creator.identity"
    if not manifest_path.is_file() or not creator_sig_path.is_file() or not identity_path.is_file():
        raise PackageManifestError("缺少 package manifest、创作者签名或 creator.identity")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PackageManifestError(f"package manifest 无法解析: {exc}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        raise PackageManifestError("不支持的 package manifest schema")
    canonical = canonical_manifest_bytes(manifest)
    if manifest_path.read_bytes() != canonical:
        raise PackageManifestError("package manifest 不是规范 JSON，拒绝歧义签名字节")
    package_type = str(manifest.get("package_type", ""))
    if package_type not in ("plugin", "template"):
        raise PackageManifestError(f"不支持的包类型: {package_type!r}")
    package_id = str(manifest.get("package_id", ""))
    version = str(manifest.get("version", ""))
    if not package_id or not version:
        raise PackageManifestError("package manifest 缺少 package_id 或 version")
    try:
        identity_data = json.loads(identity_path.read_text(encoding="utf-8"))
        creator = CreatorIdentity.from_dict(identity_data)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise PackageManifestError(f"creator.identity 无效: {exc}") from exc
    if manifest.get("creator_fingerprint") != creator.key_fingerprint:
        raise PackageManifestError("manifest 的创作者指纹与 creator.identity 公钥不一致")
    if manifest.get("requested_username") != creator.username:
        raise PackageManifestError("manifest 的 requested_username 与 creator.identity 不一致")
    _verify_ed25519(creator.public_key, creator_sig_path.read_bytes(), canonical, "创作者")

    declared = manifest.get("files")
    if not isinstance(declared, dict) or not declared:
        raise PackageManifestError("package manifest 的 files 必须是非空映射")
    actual = _payload_files(package_dir)
    if set(declared) != set(actual):
        missing = sorted(set(declared) - set(actual))
        extra = sorted(set(actual) - set(declared))
        raise PackageManifestError(f"插件包文件集合不一致：缺少={missing}，未声明={extra}")
    for rel, expected in declared.items():
        safe = _safe_relative_path(str(rel))
        if not isinstance(expected, str) or not expected.startswith("sha256:"):
            raise PackageManifestError(f"文件哈希格式非法: {rel}")
        digest = hashlib.sha256((package_dir / Path(*safe.parts)).read_bytes()).hexdigest()
        if expected != f"sha256:{digest}":
            raise PackageManifestError(f"文件哈希不一致: {rel}")

    maintainer_path = package_dir / MAINTAINER_SIGNATURE_NAME
    maintainer_signed = maintainer_path.is_file()
    if require_maintainer and not maintainer_signed:
        raise PackageManifestError("市场发布包缺少维护者签名")
    if maintainer_signed:
        if maintainer_public_key is None:
            raise PackageManifestError("存在维护者签名，但未提供维护者公钥")
        _verify_ed25519(maintainer_public_key, maintainer_path.read_bytes(), canonical, "维护者")
    return PackageVerification(
        package_type=package_type,  # type: ignore[arg-type]
        package_id=package_id,
        version=version,
        creator=creator,
        manifest_sha256=hashlib.sha256(canonical).hexdigest(),
        maintainer_signed=maintainer_signed,
    )
