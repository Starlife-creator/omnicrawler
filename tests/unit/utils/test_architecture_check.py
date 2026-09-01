"""Contract tests for the repository dependency-boundary gate."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def _load_checker() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "tools" / "check_architecture.py"
    spec = importlib.util.spec_from_file_location("check_architecture", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _check_source(tmp_path: Path, relative: str, source: str) -> list[str]:
    source_root = tmp_path / "src"
    target = source_root / "omnicrawler" / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return _load_checker().check(source_root)


def test_services_cannot_import_gui(tmp_path: Path) -> None:
    errors = _check_source(tmp_path, "services/example.py", "from ..gui import i18n\n")
    assert any("omnicrawler.gui" in error for error in errors)


def test_core_cannot_import_pdf_implementation(tmp_path: Path) -> None:
    errors = _check_source(tmp_path, "core/example.py", "from ..pdfx import templates\n")
    assert any("omnicrawler.pdfx" in error for error in errors)


def test_core_cannot_lazy_import_heavy_optional_runtime(tmp_path: Path) -> None:
    errors = _check_source(
        tmp_path,
        "core/example.py",
        "def load():\n    from paddleocr import PPStructureV3\n    return PPStructureV3\n",
    )
    assert any("optional module paddleocr" in error for error in errors)


def test_capability_probe_may_verify_optional_runtime(tmp_path: Path) -> None:
    errors = _check_source(
        tmp_path,
        "core/capabilities.py",
        "def verify():\n    from paddleocr import PPStructureV3\n    return PPStructureV3\n",
    )
    assert errors == []


def test_cycle_budget_blocks_dependency_graph_growth(tmp_path: Path) -> None:
    source_root = tmp_path / "src"
    package = source_root / "omnicrawler"
    (package / "alpha").mkdir(parents=True)
    (package / "beta").mkdir(parents=True)
    (package / "alpha" / "__init__.py").write_text(
        "from ..beta import value\n", encoding="utf-8"
    )
    (package / "beta" / "__init__.py").write_text(
        "from ..alpha import value\n", encoding="utf-8"
    )
    checker = _load_checker()
    metrics = checker.cycle_metrics(source_root)
    assert metrics == {
        "components": 1,
        "modules": 2,
        "edges": 2,
        "largest_component": 2,
    }
    errors = checker.check_cycle_budget(
        source_root,
        {"components": 0, "modules": 0, "edges": 0, "largest_component": 0},
    )
    assert len(errors) == 4
