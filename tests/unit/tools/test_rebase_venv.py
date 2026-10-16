"""rebase_venv.read_source_version 的回归。

起因：bump_version 的环境版本对账依赖 rebase_venv，而它只从
``src/omnicrawler/__init__.py`` 读 ``__version__`` 字面量。版本真源早已迁到
``_version.py``（``__init__.py`` 仅经模块级 ``__getattr__`` re-export，无字面量），
于是 ``read_source_version`` 恒为 ``None`` ⇒ 对账被**静默跳过** ⇒ 本地 editable
元数据永不刷新 ⇒ 每次 bump 该步必红且原因不可见（1.0.0 发布实测）。
"""

from __future__ import annotations

from pathlib import Path

from tools.rebase_venv import read_source_version

_REPO_ROOT = Path(__file__).resolve().parents[3]


def test_reads_version_from_the_authoritative_file(tmp_path: Path) -> None:
    """真源在 `_version.py`；`__init__.py` 无字面量时必须从中取到值。"""
    pkg = tmp_path / "src" / "omnicrawler"
    pkg.mkdir(parents=True)
    (pkg / "_version.py").write_text('__version__ = "9.9.9"\n', encoding="utf-8")
    # 迁移后的形态：__init__.py 没有模块级字面量
    (pkg / "__init__.py").write_text(
        "def __getattr__(name):\n    from ._version import __version__\n    return __version__\n",
        encoding="utf-8",
    )
    assert read_source_version(tmp_path) == "9.9.9"


def test_falls_back_to_init_when_version_file_absent(tmp_path: Path) -> None:
    """旧布局兜底：只有 `__init__.py` 带字面量时仍能读到。"""
    pkg = tmp_path / "src" / "omnicrawler"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    assert read_source_version(tmp_path) == "1.2.3"


def test_returns_none_when_no_version_is_findable(tmp_path: Path) -> None:
    """两者都无字面量 ⇒ None（调用方据此 no-op，而非误报）。"""
    pkg = tmp_path / "src" / "omnicrawler"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("pass\n", encoding="utf-8")
    assert read_source_version(tmp_path) is None


def test_repo_source_version_matches_version_module() -> None:
    """真实仓库：read_source_version 必须等于 `_version.py` 的字面量。"""
    import re

    text = (_REPO_ROOT / "src" / "omnicrawler" / "_version.py").read_text(encoding="utf-8")
    expected = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert expected is not None
    assert read_source_version(_REPO_ROOT) == expected.group(1)
