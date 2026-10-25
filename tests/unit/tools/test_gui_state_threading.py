"""C-#13：``tools/check_gui_state_threading.py`` 白名单门禁反向断言。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location(
        "_check_gui_state_threading_under_test",
        _ROOT / "tools" / "check_gui_state_threading.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECKER = _load()


def _write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_clean_synthetic_root_passes(tmp_path: Path) -> None:
    _write(tmp_path, "src/omnicrawler/gui/views/other.py", "def f() -> None:\n    return\n")
    assert CHECKER.check(tmp_path) == []


def test_sanctioned_wrapper_is_exempt(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/omnicrawler/gui/core/state_query_service.py",
        "from ...state import StateStore\n"
        "def load(db):\n    with StateStore(db) as s:\n        return s.rows('x')\n",
    )
    assert CHECKER.check(tmp_path) == []


def test_new_file_direct_connection_is_blocked(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/omnicrawler/gui/views/new_view.py",
        "def f(db):\n    with StateStore(db) as s:\n        return s.rows('x')\n",
    )
    issues = CHECKER.check(tmp_path)
    assert any("新增" in issue and "state_query_service" in issue for issue in issues), issues


def test_baseline_site_count_growth_is_blocked(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "src/omnicrawler/gui/views/developer_inspector.py",
        "def a(db):\n    with StateStore(db): pass\n"
        "def b(db):\n    with StateStore(db): pass\n"
        "def c(db):\n    with StateStore(db): pass\n",
    )
    issues = CHECKER.check(tmp_path)
    assert any("超出基线" in issue for issue in issues), issues


def test_real_repo_sites_within_baseline() -> None:
    assert CHECKER.check(_ROOT) == []
