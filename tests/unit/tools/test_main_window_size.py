"""C-#19：``tools/check_main_window_size.py`` 规模 ratchet 门禁反向断言。"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]


def _load():
    spec = importlib.util.spec_from_file_location(
        "_check_main_window_size_under_test",
        _ROOT / "tools" / "check_main_window_size.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CHECKER = _load()


def _write_main_file(root: Path, methods: dict[str, int]) -> Path:
    """生成含 MainWindow 类的合成 main.py，每个方法占给定行数。"""
    parts = ["from PySide6.QtWidgets import QMainWindow", "", "class MainWindow(QMainWindow):"]
    for name, lines in methods.items():
        parts.append(f"    def {name}(self):")
        for offset in range(lines - 2):
            parts.append(f"        # line {offset}")
        parts.append("        return None")
    path = root / "src" / "omnicrawler" / "gui" / "main.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(parts) + "\n", encoding="utf-8")
    return path


def _write_baseline(root: Path, limits: dict[str, int]) -> Path:
    path = root / "baseline.json"
    path.write_text(
        json.dumps(
            {
                "_meta": {"purpose": "test"},
                "file": "src/omnicrawler/gui/main.py",
                "class": "MainWindow",
                "limits": limits,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_measure_reports_three_metrics() -> None:
    actual = CHECKER.measure(CHECKER.MAIN_FILE)
    assert set(actual) == {"file_lines", "method_count", "longest_method_lines"}
    assert actual["file_lines"] > 0 and actual["method_count"] > 0


def test_real_repo_within_baseline() -> None:
    assert CHECKER.check(CHECKER.MAIN_FILE, CHECKER.DEFAULT_BASELINE) == []


def test_growth_beyond_baseline_is_blocked(tmp_path: Path) -> None:
    main_file = _write_main_file(tmp_path, {"setup_a": 3, "setup_b": 3})
    baseline = _write_baseline(
        tmp_path, {"file_lines": 5, "method_count": 1, "longest_method_lines": 2}
    )
    issues = CHECKER.check(main_file, baseline)
    assert len(issues) == 3, issues


def test_within_baseline_passes(tmp_path: Path) -> None:
    main_file = _write_main_file(tmp_path, {"setup_a": 2})
    actual = CHECKER.measure(main_file)
    baseline = _write_baseline(
        tmp_path,
        {
            "file_lines": actual["file_lines"] + 5,
            "method_count": actual["method_count"],
            "longest_method_lines": actual["longest_method_lines"],
        },
    )
    assert CHECKER.check(main_file, baseline) == []


def test_longest_method_span_is_detected(tmp_path: Path) -> None:
    main_file = _write_main_file(tmp_path, {"short": 2, "long": 9})
    actual = CHECKER.measure(main_file)
    assert actual["longest_method_lines"] == 9
    assert actual["method_count"] == 2
