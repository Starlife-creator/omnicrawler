"""存档解包安全工具（S3.2.2：标记 deprecated——由 core/archive_security 收敛）。

⚠ 已废弃：归档安全实现已收敛到 :mod:`omnicrawl.core.archive_security`，
本模块仅供历史调用方过渡，新代码禁止使用。
"""

from __future__ import annotations

import os
import shutil
import stat
import tarfile
import tempfile
import warnings
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO


class UnsafeArchiveError(ValueError):
    """Raised when an archive violates extraction safety limits."""


def _deprecated() -> None:
    warnings.warn(
        "fetching.archives 已废弃，请改用 omnicrawl.core.archive_security",
        DeprecationWarning,
        stacklevel=3,
    )


@dataclass(frozen=True)
class ArchiveLimits:
    max_entries: int = 10_000
    max_total_bytes: int = 2 * 1024 * 1024 * 1024
    max_file_bytes: int = 512 * 1024 * 1024
    max_compression_ratio: float = 200.0


@dataclass(frozen=True)
class ArchiveMember:
    name: str
    size: int
    compressed_size: int
    is_dir: bool
    is_link: bool
    opener: zipfile.ZipInfo | tarfile.TarInfo


def _safe_relative_path(name: str) -> Path:
    normalized = name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not normalized or not path.parts or path.is_absolute() or ".." in path.parts:
        raise UnsafeArchiveError(f"Archive member escapes destination: {name!r}")
    if ":" in path.parts[0]:
        raise UnsafeArchiveError(f"Archive member contains a drive path: {name!r}")
    return Path(*path.parts)


def _validate_members(members: Iterable[ArchiveMember], limits: ArchiveLimits) -> list[ArchiveMember]:
    checked: list[ArchiveMember] = []
    total = 0
    seen: set[str] = set()
    for member in members:
        if len(checked) >= limits.max_entries:
            raise UnsafeArchiveError(f"Archive contains more than {limits.max_entries} entries")
        relative = _safe_relative_path(member.name)
        key = relative.as_posix().casefold()
        if key in seen:
            raise UnsafeArchiveError(f"Archive contains a duplicate path: {member.name!r}")
        seen.add(key)
        if member.is_link:
            raise UnsafeArchiveError(f"Archive links are not allowed: {member.name!r}")
        if member.size < 0 or member.size > limits.max_file_bytes:
            raise UnsafeArchiveError(f"Archive member is too large: {member.name!r}")
        total += member.size
        if total > limits.max_total_bytes:
            raise UnsafeArchiveError("Archive exceeds the total uncompressed size limit")
        if member.size and member.compressed_size <= 0:
            raise UnsafeArchiveError(f"Invalid compressed size: {member.name!r}")
        if member.compressed_size and member.size / member.compressed_size > limits.max_compression_ratio:
            raise UnsafeArchiveError(f"Suspicious compression ratio: {member.name!r}")
        checked.append(member)
    return checked


def _copy_limited(source: IO[bytes], destination: Path, expected_size: int) -> None:
    written = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as output:
        while chunk := source.read(1024 * 1024):
            written += len(chunk)
            if written > expected_size:
                raise UnsafeArchiveError(f"Archive member expanded beyond declared size: {destination.name}")
            output.write(chunk)
    if written != expected_size:
        raise UnsafeArchiveError(f"Archive member size mismatch: {destination.name}")


def _zip_members(archive: zipfile.ZipFile) -> list[ArchiveMember]:
    result: list[ArchiveMember] = []
    for info in archive.infolist():
        unix_mode = (info.external_attr >> 16) & 0xFFFF
        is_link = stat.S_ISLNK(unix_mode)
        result.append(
            ArchiveMember(
                info.filename,
                info.file_size,
                info.compress_size,
                info.is_dir(),
                is_link,
                info,
            )
        )
    return result


def _tar_members(archive: tarfile.TarFile) -> list[ArchiveMember]:
    result: list[ArchiveMember] = []
    for info in archive.getmembers():
        if not (info.isfile() or info.isdir() or info.issym() or info.islnk()):
            raise UnsafeArchiveError(f"Unsupported archive member type: {info.name!r}")
        result.append(
            ArchiveMember(
                info.name,
                info.size,
                info.size,
                info.isdir(),
                info.issym() or info.islnk(),
                info,
            )
        )
    return result


def safe_extract_archive(
    archive_path: str | Path,
    destination: str | Path,
    *,
    limits: ArchiveLimits | None = None,
) -> list[Path]:
    """Extract ZIP or TAR safely and publish the destination only after success.

    已废弃（S3.2.2）：归档安全已收敛到 :mod:`omnicrawl.core.archive_security`。
    """
    _deprecated()

    source = Path(archive_path).resolve(strict=True)
    target = Path(destination).resolve()
    limits = limits or ArchiveLimits()
    if target.exists() and any(target.iterdir()):
        raise FileExistsError(f"Destination must be absent or empty: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-extract-", dir=target.parent))
    extracted: list[Path] = []
    try:
        if zipfile.is_zipfile(source):
            with zipfile.ZipFile(source) as archive:
                members = _validate_members(_zip_members(archive), limits)
                for member in members:
                    relative = _safe_relative_path(member.name)
                    output = staging / relative
                    if member.is_dir:
                        output.mkdir(parents=True, exist_ok=True)
                    else:
                        if not isinstance(member.opener, zipfile.ZipInfo):
                            raise UnsafeArchiveError("ZIP member metadata type mismatch")
                        with archive.open(member.opener) as stream:
                            _copy_limited(stream, output, member.size)
                    extracted.append(relative)
        elif tarfile.is_tarfile(source):
            with tarfile.open(source, mode="r:*") as archive:
                members = _validate_members(_tar_members(archive), limits)
                for member in members:
                    relative = _safe_relative_path(member.name)
                    output = staging / relative
                    if member.is_dir:
                        output.mkdir(parents=True, exist_ok=True)
                    else:
                        if not isinstance(member.opener, tarfile.TarInfo):
                            raise UnsafeArchiveError("TAR member metadata type mismatch")
                        tar_stream = archive.extractfile(member.opener)
                        if tar_stream is None:
                            raise UnsafeArchiveError(f"Cannot read archive member: {member.name!r}")
                        with tar_stream:
                            _copy_limited(tar_stream, output, member.size)
                    extracted.append(relative)
        else:
            raise UnsafeArchiveError(f"Unsupported or invalid archive: {source.name}")

        if target.exists():
            target.rmdir()
        os.replace(staging, target)
        return [target / path for path in extracted]
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
