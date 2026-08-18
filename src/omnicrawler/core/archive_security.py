"""Shared bounded ZIP readers for signed offline packages and upgrades."""

from __future__ import annotations

import hashlib
import os
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class UnsafePackageError(ValueError):
    """Raised when a package archive exceeds a verified local safety budget."""


@dataclass(frozen=True, slots=True)
class ZipReadLimits:
    max_entries: int = 100_000
    max_total_bytes: int = 20 * 1024**3
    max_file_bytes: int = 4 * 1024**3
    max_manifest_bytes: int = 16 * 1024**2
    max_compression_ratio: float = 200.0


DEFAULT_ZIP_READ_LIMITS = ZipReadLimits()


def validate_zip_archive(
    archive: zipfile.ZipFile,
    *,
    required: tuple[str, ...] = (),
    limits: ZipReadLimits = DEFAULT_ZIP_READ_LIMITS,
) -> dict[str, zipfile.ZipInfo]:
    """Validate archive metadata before any member body is read into memory."""
    infos = archive.infolist()
    if len(infos) > limits.max_entries:
        raise UnsafePackageError(f"package contains more than {limits.max_entries} entries")
    members: dict[str, zipfile.ZipInfo] = {}
    total = 0
    seen: set[str] = set()
    for info in infos:
        relative = _safe_relative(info.filename)
        name = relative.as_posix()
        key = name.casefold()
        if key in seen:
            raise UnsafePackageError(f"package contains a duplicate path: {info.filename!r}")
        seen.add(key)
        if info.flag_bits & 0x1:
            raise UnsafePackageError(f"encrypted package members are not supported: {info.filename!r}")
        unix_mode = (info.external_attr >> 16) & 0xFFFF
        if stat.S_ISLNK(unix_mode):
            raise UnsafePackageError(f"package links are not supported: {info.filename!r}")
        if info.is_dir():
            members[name] = info
            continue
        if info.file_size < 0 or info.file_size > limits.max_file_bytes:
            raise UnsafePackageError(f"package member is too large: {info.filename!r}")
        total += info.file_size
        if total > limits.max_total_bytes:
            raise UnsafePackageError("package exceeds the total uncompressed size limit")
        if info.file_size and info.compress_size <= 0:
            raise UnsafePackageError(f"package member has invalid compressed size: {info.filename!r}")
        # S4.5 P3#146：压缩比检查仅对足够大的成员生效（小文本文件高压缩比正常，不再误伤）
        if (
            info.compress_size
            and info.file_size >= 16 * 1024
            and info.file_size / info.compress_size > limits.max_compression_ratio
        ):
            raise UnsafePackageError(f"package member has suspicious compression ratio: {info.filename!r}")
        members[name] = info
    missing = [name for name in required if name not in members or members[name].is_dir()]
    if missing:
        raise UnsafePackageError(f"package is missing required members: {', '.join(missing)}")
    return members


def read_zip_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    maximum_bytes: int,
) -> bytes:
    """Read a small validated member while enforcing a second size bound."""
    if info.file_size > maximum_bytes:
        raise UnsafePackageError(f"package metadata member exceeds {maximum_bytes} bytes: {info.filename!r}")
    with archive.open(info) as stream:
        payload = stream.read(maximum_bytes + 1)
    if len(payload) != info.file_size or len(payload) > maximum_bytes:
        raise UnsafePackageError(f"package member size mismatch: {info.filename!r}")
    return payload


def copy_zip_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    destination: Path,
) -> str:
    """Stream one validated member to disk and return its SHA-256 digest.

    Writes to a temporary sibling and atomically renames on success, so a
    failed or oversized extraction never leaves a partial file behind.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_name = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    digest = hashlib.sha256()
    written = 0
    try:
        with archive.open(info) as source, temp_name.open("xb") as target:
            while chunk := source.read(1024 * 1024):
                written += len(chunk)
                if written > info.file_size:
                    raise UnsafePackageError(f"package member expanded beyond metadata: {info.filename!r}")
                digest.update(chunk)
                target.write(chunk)
        if written != info.file_size:
            raise UnsafePackageError(f"package member size mismatch: {info.filename!r}")
        os.replace(temp_name, destination)
    except Exception:
        temp_name.unlink(missing_ok=True)
        raise
    return digest.hexdigest()


def _safe_relative(name: str) -> PurePosixPath:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or not path.parts
        or path.is_absolute()
        or ".." in path.parts
        or ":" in path.parts[0]
    ):
        raise UnsafePackageError(f"unsafe package path: {name!r}")
    return path
