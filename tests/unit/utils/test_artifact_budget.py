from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_checker():
    project_root = Path(__file__).resolve().parents[3]
    path = project_root / "tools" / "check_artifact_budget.py"
    spec = importlib.util.spec_from_file_location("check_artifact_budget", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_artifact_budget_reports_and_rejects_growth(tmp_path: Path) -> None:
    (tmp_path / "OmniCrawler-1.0-Windows-Portable-Standard.zip").write_bytes(b"1234")
    (tmp_path / "OmniCrawler-1.0-Windows-Portable-Full.zip").write_bytes(b"123456")
    budgets = {
        "Windows": {
            "Standard": {"baseline_mib": 1, "max_bytes": 4},
            "Full": {"baseline_mib": 1, "max_bytes": 5},
        }
    }
    report, errors = _load_checker().check_artifacts(tmp_path, "Windows", budgets)
    assert report["artifacts"]["Standard"]["within_budget"] is True
    assert report["artifacts"]["Full"]["within_budget"] is False
    assert errors == ["Windows Full artifact is 0.0 MiB; budget is 0.0 MiB"]


def test_release_workflow_enforces_all_platform_budgets() -> None:
    project_root = Path(__file__).resolve().parents[3]
    workflow_root = project_root / ".github" / "workflows"
    platform_workflows = {
        "Windows": "reusable-build-windows.yml",
        "Linux": "reusable-build-linux.yml",
        "macOS": "reusable-build-macos.yml",
    }
    for platform, filename in platform_workflows.items():
        workflow = (workflow_root / filename).read_text(encoding="utf-8")
        assert workflow.count("python tools/check_artifact_budget.py --platform") == 1
        assert f"--platform {platform}" in workflow
