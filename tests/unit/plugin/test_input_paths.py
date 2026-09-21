"""插件源入口解析判据的单元测试（`plugin_input_paths`）。

这批用例锁定三件事：入口必须落在工作区内、必须命中插件声明的 `input_files`、
以及拿不到工作区时必须拒绝而不是不过滤地放行。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.core.errors import PolicyBlockedError
from omnicrawler.plugins.plugin_input_paths import resolve_input_entries


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "work"
    root.mkdir()
    return root


def test_no_entry_returns_empty(tmp_path: Path) -> None:
    assert resolve_input_entries(None, None, workspace=_workspace(tmp_path)) == (None, [])


def test_relative_entry_resolves_inside_workspace(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    file_path, files = resolve_input_entries("savedrecs.xls", ["a.csv"], workspace=root)
    assert file_path == str((root / "savedrecs.xls").resolve())
    assert files == [str((root / "a.csv").resolve())]


def test_absolute_entry_inside_workspace_is_accepted(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    target = root / "sub" / "a.csv"
    target.parent.mkdir()
    file_path, _files = resolve_input_entries(str(target), None, workspace=root)
    assert file_path == str(target.resolve())


def test_entry_escaping_workspace_is_rejected(tmp_path: Path) -> None:
    """`../` 越界必须拒绝 —— 原实现（插件侧）会静默回落成「工作区 + 文件名」。"""
    root = _workspace(tmp_path)
    with pytest.raises(PolicyBlockedError, match="escapes the workspace"):
        resolve_input_entries("../../outside.csv", None, workspace=root)


def test_absolute_entry_outside_workspace_is_rejected(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    outside = tmp_path / "outside.csv"
    outside.write_text("x", encoding="utf-8")
    with pytest.raises(PolicyBlockedError, match="escapes the workspace"):
        resolve_input_entries(str(outside), None, workspace=root)


def test_symlink_pointing_outside_workspace_is_rejected(tmp_path: Path) -> None:
    """解析跟随符号链接：链接指向区外 ⇒ 拒绝（不能只看字面路径）。

    符号链接在受限环境里可能**静默创建失败**（`symlink_to` 不抛错但不生成链接），
    因此这里显式断言前置条件——前置不成立就可见地跳过，绝不让用例假通过。
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.csv").write_text("x", encoding="utf-8")
    root = _workspace(tmp_path)
    link = root / "link.csv"
    try:
        link.symlink_to(outside / "secret.csv")
    except (OSError, NotImplementedError):  # pragma: no cover - 平台能力差异
        pytest.skip("当前环境不允许创建符号链接（Windows 需要开发者模式或特权）")
    if not link.is_symlink():  # pragma: no cover - 沙箱可能静默吞掉创建
        pytest.skip("当前环境静默忽略了符号链接创建，无法验证该逃逸路径")
    with pytest.raises(PolicyBlockedError, match="escapes the workspace"):
        resolve_input_entries("link.csv", None, workspace=root)


def test_declared_basename_glob_is_honoured(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    file_path, _files = resolve_input_entries(
        "savedrecs.xls", None, workspace=root, declared=("*.xls", "*.xlsx")
    )
    assert file_path == str((root / "savedrecs.xls").resolve())


def test_declared_path_glob_is_honoured(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    _file_path, files = resolve_input_entries(
        None, ["data/a.csv"], workspace=root, declared=("data/*.csv",)
    )
    assert files == [str((root / "data" / "a.csv").resolve())]


def test_entry_not_covered_by_declaration_is_rejected(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    with pytest.raises(PolicyBlockedError, match="input_files"):
        resolve_input_entries("savedrecs.xls", None, workspace=root, declared=("*.csv",))


def test_undeclared_plugin_is_only_confined_to_workspace(tmp_path: Path) -> None:
    """未声明 input_files 的插件不受白名单约束（只受工作区限定）。"""
    root = _workspace(tmp_path)
    file_path, _files = resolve_input_entries("anything.bin", None, workspace=root)
    assert file_path == str((root / "anything.bin").resolve())


def test_missing_workspace_with_entry_is_rejected(tmp_path: Path) -> None:
    """声明了入口却拿不到工作区 ⇒ 拒绝，而不是不过滤地放行。"""
    with pytest.raises(PolicyBlockedError, match="workspace is unknown"):
        resolve_input_entries("a.csv", None, workspace=None)


def test_non_array_files_is_rejected(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    with pytest.raises(PolicyBlockedError, match="must be an array"):
        resolve_input_entries(None, {"a": 1}, workspace=root)


def test_blank_entries_are_ignored(tmp_path: Path) -> None:
    root = _workspace(tmp_path)
    assert resolve_input_entries("  ", ["", "   "], workspace=root) == (None, [])
