from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WORKFLOWS = (
    "quality.yml",
    "e2e.yml",
    "plugin-sandbox.yml",
    "license-gate.yml",
)


def test_expensive_ci_does_not_run_twice_for_internal_pull_requests() -> None:
    """Feature pushes are covered by the PR event; direct main pushes remain covered."""
    workflow_root = PROJECT_ROOT / ".github" / "workflows"
    for name in WORKFLOWS:
        workflow = (workflow_root / name).read_text(encoding="utf-8")
        assert "  push:\n    branches: [main]" in workflow, name
        assert "  pull_request:" in workflow, name


def test_quality_collects_platform_failures_without_masking_them() -> None:
    import yaml

    workflow = yaml.safe_load((PROJECT_ROOT / ".github/workflows/quality.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["test"]
    assert job["strategy"]["fail-fast"] is False
    steps = job["steps"]
    test = next(step for step in steps if step.get("id") == "core_tests")
    assert not test.get("continue-on-error", False)
    assert "--junitxml=pytest-core.xml" in test["run"]
    coverage = next(step for step in steps if step.get("id") == "core_coverage")
    assert "steps.core_tests.outcome == 'failure'" in coverage["if"]
    gate = next(step for step in steps if "--profile core coverage.json" in step.get("run", ""))
    assert "steps.core_coverage.outcome == 'success'" in gate["if"]
    assert not gate.get("continue-on-error", False)
    report = next(step for step in steps if step.get("name") == "Preserve core test and coverage diagnostics")
    assert "steps.core_tests.outcome == 'failure'" in report["if"]
    assert "coverage.json" in report["with"]["path"]
    assert "pytest-core.xml" in report["with"]["path"]
