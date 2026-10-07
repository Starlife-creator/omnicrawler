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
        "ai",
        "document",
    }
    assert checker.check("not-a-profile") == ["unknown feature profile: not-a-profile"]


def test_quality_workflow_installs_each_feature_in_isolation() -> None:
    project_root = Path(__file__).resolve().parents[3]
    workflow = (project_root / ".github" / "workflows" / "quality.yml").read_text(
        encoding="utf-8"
    )
    job = workflow[workflow.index("  feature-install:"):workflow.index("  test:")]
    assert "profile: [html, pdf, async-http, tls, streams, storage, security, ai, document]" in job
    assert 'pip install -e ".[${{ matrix.profile }}]"' in job
    assert 'check_extra_install.py "${{ matrix.profile }}"' in job


def test_ai_transport_and_document_features_are_declared_and_installed_in_ci():
    import tomllib

    root = Path(__file__).resolve().parents[3]
    extras = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["optional-dependencies"]
    assert any(spec.startswith("aiohttp") for spec in extras.get("ai", []))
    for edition in ("full", "full-macos"):
        assert any(spec.startswith("aiohttp") for spec in extras[edition])
    workflow = (root / ".github/workflows/quality.yml").read_text(encoding="utf-8")
    core = workflow[workflow.index("  test:"):workflow.index("  docker:")]
    assert "[html,pdf,dev,async-http,gui,tls,storage,ai,document]" in core


def test_independent_ai_extra_reports_missing_transport(monkeypatch):
    checker = _load_checker()
    original = checker.importlib.import_module

    def unavailable(name):
        if name == "aiohttp":
            raise ModuleNotFoundError("transport absent")
        return original(name)

    monkeypatch.setattr(checker.importlib, "import_module", unavailable)
    assert checker.check("ai") == ["ai cannot import aiohttp: ModuleNotFoundError: transport absent"]
