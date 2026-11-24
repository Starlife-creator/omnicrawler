"""require_workspace_path：工作区包含性校验的唯一实现。

为什么要有这个文件：这个函数被声明为「所有消费外部可控路径的调用点必须经过它」，
但它**本身没有专门测试**。而它守的正是「别读到工作区外去」这条边界。

实测（2026-11-24）：`pipeline_ops/provenance.py` 没有用它，自己写了一遍
「`Path(str(...))` + resolve + parents 包含判定」，那份实现有两个毛病：

* 相对路径按**进程 cwd** 解析（`Path(str)` 不挂基准目录）⇒ cwd ≠ workspace 时
  合法记录被误判越界；
* 「路径**等于** workspace」时 `workspace not in path.parents` 成立
  （parents 不含自身）⇒ 与统一工具口径不一致。

本文件把统一工具的行为钉住，避免下一个调用点再「造一份自己的」：
相对路径按 **workspace** 解析、越界拒绝、符号链接按**解析后**判定。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from omnicrawler.security.paths import require_workspace_path


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    (root / "artifacts").mkdir(parents=True)
    (root / "artifacts" / "a.pdf").write_bytes(b"%PDF-1.4 x")
    return root


def test_relative_path_resolves_against_the_workspace_not_cwd(
    workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """相对路径按 **workspace** 解析 —— 这是本函数存在的核心理由。"""
    expected = (workspace / "artifacts" / "a.pdf").resolve()
    for cwd in (tmp_path, workspace, workspace / "artifacts"):
        monkeypatch.chdir(cwd)
        assert require_workspace_path("artifacts/a.pdf", root=workspace) == expected


def test_absolute_path_inside_the_workspace_is_accepted(workspace: Path) -> None:
    target = workspace / "artifacts" / "a.pdf"
    assert require_workspace_path(str(target), root=workspace) == target.resolve()


def test_the_workspace_root_itself_is_accepted(workspace: Path) -> None:
    """路径**等于** workspace 时合法（"in path.parents" 写法会漏掉这一条）。"""
    assert require_workspace_path(str(workspace), root=workspace) == workspace.resolve()
    assert require_workspace_path(".", root=workspace) == workspace.resolve()


@pytest.mark.parametrize("value", ["../outside.pdf", "..", "artifacts/../../outside.pdf"])
def test_parent_traversal_is_rejected(workspace: Path, tmp_path: Path, value: str) -> None:
    """`..` 必须拒绝 —— 这是这条守卫的全部意义。"""
    (tmp_path / "outside.pdf").write_bytes(b"x")
    with pytest.raises(ValueError, match="越出工作区"):
        require_workspace_path(value, root=workspace)


def test_absolute_path_outside_the_workspace_is_rejected(workspace: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"x")
    with pytest.raises(ValueError, match="越出工作区"):
        require_workspace_path(str(outside), root=workspace)


def test_symlink_is_judged_by_its_resolved_target(workspace: Path, tmp_path: Path) -> None:
    """符号链接按**解析后**的位置判定，不能靠「链接本身在区内」绕过。"""
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"x")
    link = workspace / "artifacts" / "link.pdf"
    try:
        os.symlink(outside, link)
    except (OSError, NotImplementedError):
        pytest.skip("本环境不允许创建符号链接")
    with pytest.raises(ValueError, match="越出工作区"):
        require_workspace_path(str(link), root=workspace)


def test_the_what_label_appears_in_the_error(workspace: Path) -> None:
    """报错要带上下文（哪个字段越界），便于定位。"""
    with pytest.raises(ValueError, match="PDF 交付路径"):
        require_workspace_path("../x.pdf", root=workspace, what="PDF 交付路径")


def test_tilde_is_expanded_before_the_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`~` 先展开再判定 —— 否则相对语义会把它当普通文件名而「合法」通过。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    ws = tmp_path / "ws"
    ws.mkdir()
    with pytest.raises(ValueError, match="越出工作区"):
        require_workspace_path("~/x.pdf", root=ws)
