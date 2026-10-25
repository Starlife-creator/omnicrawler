"""`tools/check_dependency_consistency.py` 门禁反向断言（C-#14 / C-#20）。

每一类漂移都在临时目录的合成 pyproject 上装回去，断言脚本判红；干净声明必须通过。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location(
        "_check_dependency_consistency_under_test",
        _ROOT / "tools" / "check_dependency_consistency.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECKER = _load()


def _write(root: Path, *, base: list[str] | None = None, extras: dict[str, list[str]] | None = None) -> None:
    lines = [
        "[project]",
        'name = "demo"',
        'version = "0.1.0"',
        'requires-python = ">=3.12"',
        f"dependencies = [{', '.join(repr(req) for req in (base or ['PyYAML>=6,<7']))}]",
        "",
        "[project.optional-dependencies]",
    ]
    for extra, requirements in (extras or {"html": ["beautifulsoup4>=4.12,<5"]}).items():
        lines.append(f"{extra} = [{', '.join(repr(req) for req in requirements)}]")
    (root / "pyproject.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_clean_pyproject_passes(tmp_path: Path) -> None:
    _write(tmp_path)
    assert CHECKER.check(tmp_path) == []


def test_two_opencv_variants_are_red(tmp_path: Path) -> None:
    _write(
        tmp_path,
        extras={
            "ocr": ["opencv-python>=4.10,<6", "opencv-contrib-python>=4.10,<6"],
        },
    )
    issues = CHECKER.check(tmp_path)
    assert any("多个变体" in issue for issue in issues), issues


def test_dead_dependency_is_red(tmp_path: Path) -> None:
    _write(tmp_path, extras={"full": ["aiofiles>=25,<26"]})
    issues = CHECKER.check(tmp_path)
    assert any("aiofiles" in issue and "死依赖" in issue for issue in issues), issues


def test_unbounded_dependency_is_red(tmp_path: Path) -> None:
    _write(tmp_path, extras={"full": ["requests>=2.28"]})
    issues = CHECKER.check(tmp_path)
    assert any("版本上限" in issue for issue in issues), issues


def test_real_repo_declarations_pass() -> None:
    issues = CHECKER.check(_ROOT)
    assert issues == [], issues
