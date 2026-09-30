"""Shared bounded ZIP readers for signed offline packages and upgrades."""

from __future__ import annotations

import hashlib
import logging
import os
import stat
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

logger = logging.getLogger(__name__)


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


def zip_member_mode(info: zipfile.ZipInfo) -> int | None:
    """从 zip 成员的 ``external_attr`` 取 POSIX 权限位；没有记录 ⇒ ``None``。

    ★★ 为什么必须有这条往返（2026-09-30 发现的缺陷）：zip 把 ``st_mode`` 放在
    ``external_attr`` 的高 16 位，而客户端此前**完全没读它**，一律用"新建文件的默认模式"写出
    （``shutil.copy2`` 只保留**源**文件模式，而源是刚解出来的临时文件）。后果是
    **Linux/macOS 上经变更包或整包替换后的 ``omnicrawler`` 变成不可执行** —— 应用直接起不来。
    Windows 上这个位没有意义（``os.chmod`` 只切只读），所以此前一直没暴露。

    安全钳制（包是**不可信容器**：变更包本身不签名，只有成员哈希来自已签名清单）：
    ``mode & 0o777`` —— 只保留 rwx，**丢掉 setuid/setgid/sticky**。
    否则一个被投毒的包就能给落下来的文件设特权位。
    """
    raw = (info.external_attr >> 16) & 0xFFFF
    mode = raw & 0o777
    if not mode:
        # 0 表示"没记录"（很多工具产出的 zip 就是这样）：**什么都不做**，
        # 保持当前默认行为，免得把本来正常的包改坏。
        return None
    return mode


def apply_zip_member_mode(info: zipfile.ZipInfo, path: Path) -> int | None:
    """把成员记录的权限位落到文件上（Windows 上无害：只影响只读位）。"""
    mode = zip_member_mode(info)
    if mode is None:
        return None
    try:
        path.chmod(mode)
    except OSError:
        # chmod 失败不阻断落地：内容哈希才是安全判据，权限位是正确性优化。
        # 但也不能静默 —— 权限位丢了正是"应用起不来"那类故障。
        logger.warning("无法设置文件权限位 %o: %s", mode, path)
        return None
    return mode


def copy_zip_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    destination: Path,
) -> str:
    """Stream one validated member to disk and return its SHA-256 digest.

    Writes to a temporary sibling and atomically renames on success, so a
    failed or oversized extraction never leaves a partial file behind.

    权限位（``external_attr``）在**改名之前**落到临时文件上，随后的 ``os.replace``
    把这个模式一并带过去 —— 于是"改权限"与"落地"仍然是**一次原子替换**。
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
        apply_zip_member_mode(info, temp_name)
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
