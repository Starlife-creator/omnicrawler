"""`check_lockfile_consistency` 的自测——重点证明它**能失败**。

`优化方案.md` §7.1 的依赖门禁有两条验收：「干净机器按锁文件复现环境」与
「release 产物依赖与锁一致」。本文件锁住检查器的判定能力：对每一类漂移都必须报错，
并且对真实仓库当前状态给出「通过」（否则门禁一上线就是红的）。
"""

from __future__ import annotations

import textwrap
from pathlib import Path

from tools.check_lockfile_consistency import check

_PYPROJECT = """
[project]
name = "demo"
version = "1.2.3"
requires-python = ">=3.12"
dependencies = ["PyYAML>=6,<7", "defusedxml>=0.7,<1"]

[project.optional-dependencies]
html = ["beautifulsoup4>=4.12,<5"]
pdf = ["pypdf>=5,<6"]
"""

#: 与上面 pyproject 对应的锁文件内容（一致基线）。
_LOCK = """
version = 1
requires-python = ">=3.12"

[[package]]
name = "demo"
version = "1.2.3"
source = { editable = "." }
dependencies = [
    { name = "defusedxml" },
    { name = "pyyaml" },
]

[package.optional-dependencies]
html = [{ name = "beautifulsoup4" }]
pdf = [{ name = "pypdf" }]

[package.metadata]
requires-dist = [
    { name = "defusedxml", specifier = ">=0.7,<1" },
    { name = "pyyaml", specifier = ">=6,<7" },
    { name = "beautifulsoup4", marker = "extra == 'html'", specifier = ">=4.12,<5" },
    { name = "pypdf", marker = "extra == 'pdf'", specifier = ">=5,<6" },
]
"""


def _write(tmp_path: Path, pyproject: str, lock: str) -> tuple[Path, Path]:
    pyproject_path = tmp_path / "pyproject.toml"
    lock_path = tmp_path / "uv.lock"
    pyproject_path.write_text(textwrap.dedent(pyproject), encoding="utf-8")
    lock_path.write_text(textwrap.dedent(lock), encoding="utf-8")
    return pyproject_path, lock_path


def _issues(tmp_path: Path, pyproject: str = _PYPROJECT, lock: str = _LOCK) -> list[str]:
    pyproject_path, lock_path = _write(tmp_path, pyproject, lock)
    return check(pyproject_path, lock_path)


def test_consistent_pair_has_no_issues(tmp_path: Path) -> None:
    assert _issues(tmp_path) == []


def test_requires_python_drift_is_reported(tmp_path: Path) -> None:
    lock = _LOCK.replace('requires-python = ">=3.12"', 'requires-python = ">=3.11"')
    assert any("requires-python" in item for item in _issues(tmp_path, lock=lock))


def test_version_drift_is_reported(tmp_path: Path) -> None:
    lock = _LOCK.replace('version = "1.2.3"', 'version = "1.2.2"')
    assert any("版本不一致" in item for item in _issues(tmp_path, lock=lock))


def test_declared_dependency_missing_from_lock_is_reported(tmp_path: Path) -> None:
    lock = _LOCK.replace('    { name = "pyyaml" },\n', "")
    issues = _issues(tmp_path, lock=lock)
    assert any("顶层依赖未进入锁文件" in item and "pyyaml" in item for item in issues)


def test_locked_dependency_not_declared_is_reported(tmp_path: Path) -> None:
    lock = _LOCK.replace('    { name = "defusedxml" },', '    { name = "defusedxml" },\n    { name = "requests" },')
    issues = _issues(tmp_path, lock=lock)
    assert any("已从 pyproject 移除" in item and "requests" in item for item in issues)


def test_extras_group_missing_from_lock_is_reported(tmp_path: Path) -> None:
    lock = _LOCK.replace("pdf = [{ name = \"pypdf\" }]\n", "").replace(
        "    { name = \"pypdf\", marker = \"extra == 'pdf'\", specifier = \">=5,<6\" },\n", ""
    )
    issues = _issues(tmp_path, lock=lock)
    assert any("extras 组未进入锁文件" in item and "pdf" in item for item in issues)


def test_extras_group_removed_from_pyproject_is_reported(tmp_path: Path) -> None:
    """锁里保留了 pyproject 已删除的组——同样是漂移，必须报出来。"""
    lock = _LOCK.replace(
        'pdf = [{ name = "pypdf" }]',
        'pdf = [{ name = "pypdf" }]\nlegacy = [{ name = "pypdf" }]',
    )
    issues = _issues(tmp_path, lock=lock)
    assert any("已从 pyproject 移除" in item and "legacy" in item for item in issues)


def test_extras_without_marker_is_reported(tmp_path: Path) -> None:
    """extras 组在锁里没有依赖记录（空组或锁没跟上）也必须报出来。"""
    lock = _LOCK.replace(
        "    { name = \"pypdf\", marker = \"extra == 'pdf'\", specifier = \">=5,<6\" },\n", ""
    )
    issues = _issues(tmp_path, lock=lock)
    assert any("没有任何依赖记录" in item and "pdf" in item for item in issues)


def test_missing_lockfile_is_reported(tmp_path: Path) -> None:
    pyproject_path, _ = _write(tmp_path, _PYPROJECT, _LOCK)
    missing = tmp_path / "nope.lock"
    issues = check(pyproject_path, missing)
    assert any("缺少锁文件" in item for item in issues)


def test_missing_project_entry_is_reported(tmp_path: Path) -> None:
    lock = _LOCK.replace('name = "demo"', 'name = "something-else"')
    issues = _issues(tmp_path, lock=lock)
    assert any("没有项目自身的条目" in item for item in issues)


def test_real_repository_is_consistent() -> None:
    """仓库当前状态必须通过——否则这条门禁一上线就是红的（默认参数即真实路径）。"""
    assert check() == []
