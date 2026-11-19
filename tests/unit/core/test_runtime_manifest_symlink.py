"""清单必须按**文件系统真实形态**记录条目：符号链接不跟随目标。

## 为什么这条重要

macOS `.app` 里的 `.dylib` / `python3.13` 等是符号链接。早先的实现用
`path.stat()`（**跟随链接**）记录大小与哈希，而 `tar` 打包后解压出来的是
**链接本身**（几十字节）。两侧量不是同一个东西 ⇒ 校验必然报
`runtime manifest size mismatch`。

Python 3.13 的 macOS framework 链接比 3.12 更多，所以升级后一次性全暴露
（真实 CI：几十个 `.dylib` + `python3.13` + `playwright/driver/node` 全部 mismatch）。

## 判据

1. symlink 条目带 `link: True`，`sha256` 以 `symlink:` 前缀；
2. 记录的 `bytes` 是**链接目标字符串**的长度，不是目标文件大小；
3. `create` 与 `verify` **用同一个口径** —— 否则永远 mismatch；
4. 普通文件仍按内容哈希（不能被这次改动影响）。

## 本机限制

Windows 默认不允许创建符号链接（`OSError: [WinError 1314]`，需要开发者模式
或管理员权限）。所以这里用 `monkeypatch` 模拟 `Path.is_symlink` / `os.readlink`，
把判据本身测到；真实 macOS 行为由 CI 的产物体检覆盖。
"""

from __future__ import annotations

import hashlib
import pathlib

import pytest

from omnicrawler.core import runtime_manifest as rm


def _make_tree(root: pathlib.Path) -> pathlib.Path:
    (root / "plain.txt").write_bytes(b"world")
    (root / "target.txt").write_bytes(b"payload-bytes")
    return root


def test_plain_files_recorded_by_content(tmp_path: pathlib.Path) -> None:
    """普通文件按内容记录（不能被 symlink 改动影响）。"""
    _make_tree(tmp_path)
    manifest = rm.create_runtime_manifest(tmp_path)

    entry = manifest["files"]["plain.txt"]
    assert not entry.get("link")
    assert entry["bytes"] == len(b"world")
    assert entry["sha256"] == hashlib.sha256(b"world").hexdigest()


def test_symlink_recorded_as_link(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """符号链接必须记录成 `link: True` + 链接目标长度。"""
    _make_tree(tmp_path)
    # 真实建一个文件：`rglob` 只列出真实存在的路径，纯 monkeypatch 造不出条目
    link_path = tmp_path / "link.txt"
    link_path.write_bytes(b"this-is-a-plain-file-but-treated-as-link")
    target = "target.txt"

    real_is_symlink = pathlib.Path.is_symlink
    real_readlink = rm.os.readlink

    def fake_is_symlink(self: pathlib.Path) -> bool:
        if self == link_path:
            return True
        return real_is_symlink(self)

    def fake_readlink(path: object) -> str:
        if pathlib.Path(str(path)) == link_path:
            return target
        return real_readlink(path)  # type: ignore[arg-type]

    monkeypatch.setattr(pathlib.Path, "is_symlink", fake_is_symlink)
    monkeypatch.setattr(rm.os, "readlink", fake_readlink)

    manifest = rm.create_runtime_manifest(tmp_path)
    entry = manifest["files"]["link.txt"]

    assert entry.get("link") is True, "symlink 未被标记"
    assert entry["bytes"] == len(target.encode()), "记的不是链接目标长度"
    assert entry["sha256"].startswith(rm._SYMLINK_TAG), "缺少 symlink 前缀"
    assert entry["sha256"] == rm._SYMLINK_TAG + hashlib.sha256(
        target.encode()
    ).hexdigest()


def test_verify_uses_same_convention_for_symlink(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """create 与 verify 必须同一口径 —— 否则永远 mismatch。

    这条是那个真实缺陷的直接复现：早先 create 记目标大小、verify 也读
    `stat()`（跟随），但打包后磁盘上是链接本身。
    """
    _make_tree(tmp_path)
    # 真实建一个文件：`rglob` 只列出真实存在的路径，纯 monkeypatch 造不出条目
    link_path = tmp_path / "link.txt"
    link_path.write_bytes(b"this-is-a-plain-file-but-treated-as-link")
    target = "target.txt"

    real_is_symlink = pathlib.Path.is_symlink
    real_readlink = rm.os.readlink

    def fake_is_symlink(self: pathlib.Path) -> bool:
        return True if self == link_path else real_is_symlink(self)

    def fake_readlink(path: object) -> str:
        if pathlib.Path(str(path)) == link_path:
            return target
        return real_readlink(path)  # type: ignore[arg-type]

    monkeypatch.setattr(pathlib.Path, "is_symlink", fake_is_symlink)
    monkeypatch.setattr(rm.os, "readlink", fake_readlink)

    rm.create_runtime_manifest(tmp_path)
    result = rm.verify_runtime_manifest(tmp_path)

    assert result["ok"], f"对称口径下仍然失败：{result}"
    assert result["status"] == "valid"
    assert not result["corrupt"], f"被判 corrupt：{result['corrupt']}"


def test_symlink_target_change_is_detected(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """链接目标变了必须被判 corrupt —— 修复不能变成「链接一律放行」。"""
    _make_tree(tmp_path)
    (tmp_path / "other.txt").write_bytes(b"other")
    link_path = tmp_path / "link.txt"
    link_path.write_bytes(b"placeholder-for-link")
    current = {"target": "target.txt"}

    real_is_symlink = pathlib.Path.is_symlink
    real_readlink = rm.os.readlink

    def fake_is_symlink(self: pathlib.Path) -> bool:
        return True if self == link_path else real_is_symlink(self)

    def fake_readlink(path: object) -> str:
        if pathlib.Path(str(path)) == link_path:
            return current["target"]
        return real_readlink(path)  # type: ignore[arg-type]

    monkeypatch.setattr(pathlib.Path, "is_symlink", fake_is_symlink)
    monkeypatch.setattr(rm.os, "readlink", fake_readlink)

    rm.create_runtime_manifest(tmp_path)
    current["target"] = "other.txt"  # 链接被改指向别处
    result = rm.verify_runtime_manifest(tmp_path)

    assert not result["ok"], "链接目标变化未被检出"
    assert "link.txt" in result["corrupt"]
