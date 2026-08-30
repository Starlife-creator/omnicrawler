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
