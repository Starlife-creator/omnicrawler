from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pytest

from omnicrawler.core.archive_security import (
    UnsafePackageError,
    copy_zip_member,
    validate_zip_archive,
    zip_member_mode,
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
        from omnicrawler.core.utils import sha256_bytes

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


# ── 权限位往返（2026-09-30 修）─────────────────────────────────────────────
# 缺陷：zip 把 st_mode 放在 external_attr 高 16 位，而客户端**完全没读它**，一律用默认模式
# 写出 ⇒ Linux/macOS 上经变更包/整包替换后的 omnicrawler 变成**不可执行**（应用起不来）。
# Windows 上这个位没有意义，所以此前一直没暴露。


def _member_with_mode(mode: int, name: str = "omnicrawler") -> zipfile.ZipInfo:
    """带权限位的成员。★ `name` 必须可传：同一归档里放两个同名成员会被
    `validate_zip_archive` 判成重复路径（**只在 POSIX 上跑的那条端到端用例**因此亮红过 ——
    本地 Windows 跳过，所以是 CI 先发现的）。"""
    info = zipfile.ZipInfo(name)
    info.external_attr = (mode & 0xFFFF) << 16
    return info


def test_zip_member_mode_reads_posix_bits() -> None:
    assert zip_member_mode(_member_with_mode(0o755)) == 0o755
    assert zip_member_mode(_member_with_mode(0o644)) == 0o644


def test_zip_member_mode_clamps_privilege_bits() -> None:
    """★★ 安全钳制：setuid/setgid/sticky 必须被丢掉。

    变更包本身**不签名**（只有成员哈希来自已签名清单）⇒ 它是**不可信容器**。
    若不钳制，一个被投毒的包就能让落下来的文件带上特权位。
    """
    assert zip_member_mode(_member_with_mode(0o4755)) == 0o755   # setuid
    assert zip_member_mode(_member_with_mode(0o2755)) == 0o755   # setgid
    assert zip_member_mode(_member_with_mode(0o1777)) == 0o777   # sticky
    assert zip_member_mode(_member_with_mode(0o7777)) == 0o777   # 全带上


def test_zip_member_mode_absent_is_none() -> None:
    """没有权限位记录（很多工具产出的 zip 如此）⇒ None ⇒ **保持默认行为**，不改坏正常的包。"""
    assert zip_member_mode(zipfile.ZipInfo("plain")) is None
    assert zip_member_mode(_member_with_mode(0)) is None


@pytest.mark.skipif(sys.platform == "win32", reason="Windows 的 chmod 只管只读位，观察不到 rwx")
def test_copy_zip_member_applies_executable_bit(tmp_path: Path) -> None:
    """端到端：归档里记 0o755 的成员解出来后**必须可执行**（Linux/macOS 上就是应用本体）。"""
    package = tmp_path / "mode.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(_member_with_mode(0o755, "omnicrawler"), b"#!/bin/sh\necho hi\n")
        archive.writestr(_member_with_mode(0o644, "data"), b"data")

    target = tmp_path / "out" / "omnicrawler"
    with zipfile.ZipFile(package) as archive:
        members = validate_zip_archive(archive)
        copy_zip_member(archive, members["omnicrawler"], target)
        copy_zip_member(archive, members["data"], tmp_path / "out" / "data")

    assert target.stat().st_mode & 0o111, "可执行位必须被还原，否则应用起不来"
    assert (tmp_path / "out" / "data").stat().st_mode & 0o111 == 0, "数据文件不该被误设可执行"


@pytest.mark.skipif(sys.platform == "win32", reason="同上")
def test_copy_zip_member_never_sets_privilege_bits(tmp_path: Path) -> None:
    """★ 端到端的安全断言：归档里写 0o4755 ⇒ 落盘必须是 0o755（没有 setuid）。"""
    package = tmp_path / "setuid.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(_member_with_mode(0o4755), b"binary")

    target = tmp_path / "out" / "omnicrawler"
    with zipfile.ZipFile(package) as archive:
        members = validate_zip_archive(archive)
        copy_zip_member(archive, members["omnicrawler"], target)

    assert target.stat().st_mode & 0o4000 == 0, "绝不允许把 setuid 位落到文件上"
    assert target.stat().st_mode & 0o111, "但可执行位应当保留"


def test_copy_zip_member_actually_calls_the_mode_applier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 接线断言（与平台无关）：`copy_zip_member` **确实**调用了权限位还原。

    上面那两条端到端断言在 Windows 上会被跳过（`chmod` 观察不到 rwx）⇒ 若没有这一条，
    "调用被删掉"这个回归在本地与 Windows CI 上都**看不见**，只有 ubuntu 腿会红。
    这里用替身把"调用发生过"钉死，任何平台都能立刻发现接线断了。
    """
    from omnicrawler.core import archive_security as module

    package = tmp_path / "wired.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(_member_with_mode(0o755), b"binary")

    seen: list[tuple[str, int | None]] = []
    real = module.apply_zip_member_mode

    def _recorder(info: zipfile.ZipInfo, path: Path) -> int | None:
        result = real(info, path)
        seen.append((info.filename, result))
        return result

    monkeypatch.setattr(module, "apply_zip_member_mode", _recorder)
    with zipfile.ZipFile(package) as archive:
        members = validate_zip_archive(archive)
        module.copy_zip_member(archive, members["omnicrawler"], tmp_path / "out" / "omnicrawler")

    assert seen == [("omnicrawler", 0o755)], "权限位还原必须被调用一次，且拿到钳制后的模式"
