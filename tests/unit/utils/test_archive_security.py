from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from omnicrawl.core.archive_security import (
    UnsafePackageError,
    copy_zip_member,
    validate_zip_archive,
)


def test_rejects_duplicate_case_insensitive_zip_paths(tmp_path: Path) -> None:
    package = tmp_path / "duplicate.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("Models/item.bin", b"first")
        archive.writestr("models/ITEM.bin", b"second")

    with zipfile.ZipFile(package) as archive, pytest.raises(UnsafePackageError, match="duplicate"):
        validate_zip_archive(archive)


def test_rejects_suspicious_compression_ratio_before_reading_payload(tmp_path: Path) -> None:
    package = tmp_path / "compressed.zip"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("component.json", b"{}")
        archive.writestr("payload.bin", b"0" * 1_000_000)

    with zipfile.ZipFile(package) as archive, pytest.raises(UnsafePackageError, match="compression ratio"):
        validate_zip_archive(archive, required=("component.json",))


def test_rejects_dot_and_dot_slash_members_without_index_error(tmp_path: Path) -> None:
    """S1.3.2：`.` / `./` 成员之前必须先判空 parts，拒绝而不是 IndexError。"""
    for member_name in (".", "./"):
        package = tmp_path / f"dot-{member_name.replace('/', '_')}.zip"
        with zipfile.ZipFile(package, "w") as archive:
            archive.writestr(member_name, b"payload")

        with zipfile.ZipFile(package) as archive, pytest.raises(UnsafePackageError, match="unsafe package path"):
            validate_zip_archive(archive)


def test_rejects_backslash_and_drive_traversal(tmp_path: Path) -> None:
    """S1.3.2：反斜杠目录穿越与 Windows 盘符成员均被拒绝。"""
    package = tmp_path / "traversal.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(r"..\..\evil.txt", b"bad")
        archive.writestr("C:/win.txt", b"bad")

    with zipfile.ZipFile(package) as archive, pytest.raises(UnsafePackageError, match="unsafe package path"):
        validate_zip_archive(archive)


def test_copy_zip_member_atomic_no_partial_file(tmp_path: Path) -> None:
    """S1.3.2：copy_zip_member 失败时不留下残缺文件，成功时内容与摘要正确。"""
    package = tmp_path / "members.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("good.bin", b"hello" * 100)

    with zipfile.ZipFile(package) as archive:
        members = validate_zip_archive(archive, required=("good.bin",))
        good = members["good.bin"]
        from omnicrawl.core.utils import sha256_bytes

        digest = copy_zip_member(archive, good, tmp_path / "out" / "good.bin")
        assert (tmp_path / "out" / "good.bin").read_bytes() == b"hello" * 100
        assert digest == sha256_bytes(b"hello" * 100)
        assert not list((tmp_path / "out").glob(".*.tmp"))

        # 伪造超限元数据：file_size 大于实际内容 → 失败且目标不存在、无临时残留
        import copy

        inflated = copy.copy(good)
        inflated.file_size = 1_000_000
        with pytest.raises(UnsafePackageError):
            copy_zip_member(archive, inflated, tmp_path / "out" / "partial.bin")
        assert not (tmp_path / "out" / "partial.bin").exists()
        assert not list((tmp_path / "out").glob(".*.tmp"))
