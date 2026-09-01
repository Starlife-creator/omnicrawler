from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_checker():
    project_root = Path(__file__).resolve().parents[3]
    path = project_root / "tools" / "check_extra_install.py"
    spec = importlib.util.spec_from_file_location("check_extra_install", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_major_feature_profiles_have_independent_import_contracts() -> None:
    checker = _load_checker()
    assert set(checker.PROFILE_IMPORTS) == {
        "html",
        "pdf",
        "async-http",
        "tls",
        "streams",
        "storage",
        "security",
    }
    assert checker.check("not-a-profile") == ["unknown feature profile: not-a-profile"]


def test_quality_workflow_installs_each_feature_in_isolation() -> None:
    project_root = Path(__file__).resolve().parents[3]
    workflow = (project_root / ".github" / "workflows" / "quality.yml").read_text(
        encoding="utf-8"
    )
    job = workflow[workflow.index("  feature-install:"):workflow.index("  test:")]
    assert "profile: [html, pdf, async-http, tls, streams, storage, security]" in job
    assert 'pip install -e ".[${{ matrix.profile }}]"' in job
    assert 'check_extra_install.py "${{ matrix.profile }}"' in job
