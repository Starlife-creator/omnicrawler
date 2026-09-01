from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_quality_workflow_has_base_only_install_smoke() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "quality.yml").read_text(
        encoding="utf-8"
    )
    job = workflow[
        workflow.index("  minimal-install:"):workflow.index("  feature-install:")
    ]
    editable_installs = [line.strip() for line in job.splitlines() if "pip install -e" in line]
    assert editable_installs == ["- run: python -m pip install -e ."]
    assert "python tools/check_minimal_install.py" in job
    assert "python tools/check_architecture.py" in job
