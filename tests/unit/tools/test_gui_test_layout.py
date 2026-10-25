"""C-#30：``tools/check_gui_test_layout.py`` 归属规则反向断言。"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location(
        "_check_gui_test_layout_under_test",
        _ROOT / "tools" / "check_gui_test_layout.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECKER = _load()


def _layout(root: Path, *, unit: dict[str, str] | None = None, gui: dict[str, str] | None = None) -> None:
    for directory, files in (("tests/unit/gui", unit or {}), ("tests/gui", gui or {})):
        target = root / directory
        target.mkdir(parents=True, exist_ok=True)
        for name, text in files.items():
            (target / name).write_text(text, encoding="utf-8")


def test_clean_synthetic_layout_passes(tmp_path: Path) -> None:
    _layout(
        tmp_path,
        unit={"test_foo.py": "def test_ok(): assert True\n"},
        gui={"test_flow.py": "def test_flow(): assert True\n"},
    )
    assert CHECKER.check(tmp_path) == []


def test_duplicate_basename_is_blocked(tmp_path: Path) -> None:
    _layout(
        tmp_path,
        unit={"test_shared.py": "# unit\n"},
        gui={"test_shared.py": "# integration\n"},
    )
    issues = CHECKER.check(tmp_path)
    assert any("同名" in issue and "跨目录" in issue for issue in issues), issues


def test_unit_may_not_import_gui_dir(tmp_path: Path) -> None:
    _layout(
        tmp_path,
        unit={"test_a.py": "from tests.gui.helpers import x\n"},
        gui={"test_b.py": "def test_b(): pass\n"},
    )
    issues = CHECKER.check(tmp_path)
    assert any("tests/unit/gui" in issue and "不得 import tests/gui" in issue for issue in issues), issues


def test_gui_may_not_import_unit_gui_dir(tmp_path: Path) -> None:
    _layout(
        tmp_path,
        unit={"test_a.py": "def helper(): return 1\n"},
        gui={"test_b.py": "from tests.unit.gui.test_a import helper\n"},
    )
    issues = CHECKER.check(tmp_path)
    assert any("tests/gui" in issue and "不得 import tests/unit/gui" in issue for issue in issues), issues


def test_overlap_report_groups_shared_modules(tmp_path: Path) -> None:
    _layout(
        tmp_path,
        unit={"test_a.py": "from omnicrawler.gui.settings import AppSettings\n"},
        gui={"test_b.py": "from omnicrawler.gui.settings import AppSettings\n"},
    )
    overlap = dict((module, (unit_refs, gui_refs)) for module, unit_refs, gui_refs in CHECKER.overlap_report(tmp_path))
    assert "settings" in overlap
    assert overlap["settings"] == (["test_a.py"], ["test_b.py"])


def test_real_repo_layout_passes() -> None:
    assert CHECKER.check(_ROOT) == []
