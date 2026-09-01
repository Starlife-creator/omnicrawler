from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_ROOT = PROJECT_ROOT / ".github" / "workflows"
CALLER = "release.yml"
BUILD_WORKFLOWS = {
    "windows": "reusable-build-windows.yml",
    "linux": "reusable-build-linux.yml",
    "macos": "reusable-build-macos.yml",
}
FINALIZE = "reusable-finalize-release.yml"


def _workflow(name: str) -> str:
    return (WORKFLOW_ROOT / name).read_text(encoding="utf-8")


def test_release_preflight_runs_before_reusable_platform_builds() -> None:
    workflow = _workflow(CALLER)
    preflight = workflow.index("发布前快速门禁")
    windows_call = workflow.index("uses: ./.github/workflows/reusable-build-windows.yml")
    assert preflight < windows_call
    for command in (
        "python -m compileall -q src tests examples tools",
        "python -m ruff check src tests tools",
        "python tools/check_release_integrity.py",
        "python tools/check_architecture.py",
        "python tools/check_docs_consistency.py",
        "python tools/check_network_boundaries.py --source-root src/omnicrawler",
    ):
        assert command in workflow


def test_reusable_workflow_boundaries_and_explicit_inputs() -> None:
    caller = _workflow(CALLER)
    assert caller.count("uses: ./.github/workflows/reusable-") == 4
    assert "secrets: inherit" not in caller
    assert caller.count("needs.verify-python-version.outputs.build_python_version") == 4
    assert caller.count("needs.verify-python-version.outputs.asset_max_bytes") == 3

    for filename in (*BUILD_WORKFLOWS.values(), FINALIZE):
        workflow = _workflow(filename)
        assert "on:\n  workflow_call:" in workflow
        assert "build_python_version:" in workflow
        assert "${{ inputs.build_python_version }}" in workflow

    for filename in BUILD_WORKFLOWS.values():
        workflow = _workflow(filename)
        assert "asset_max_bytes:" in workflow
        assert "${{ inputs.asset_max_bytes }}" in workflow


def test_manual_dispatch_cannot_publish_a_branch_as_a_release() -> None:
    caller = _workflow(CALLER)
    finalize = _workflow(FINALIZE)
    assert "workflow_dispatch:" in caller
    assert "if: github.event_name == 'push'\n        uses: softprops/action-gh-release@" in finalize
    assert "if: github.event_name == 'push'\n        env:\n          GH_TOKEN:" in finalize


def test_all_portable_platforms_receive_attestations_and_stable_cache_keys() -> None:
    workflows = [_workflow(filename) for filename in BUILD_WORKFLOWS.values()]
    combined = "\n".join(workflows)
    assert combined.count("uses: actions/attest@") == 3
    assert "predicate-type:" not in combined
    assert 'predicate: "{}"' not in combined
    assert combined.count("为便携包生成 SLSA provenance") == 3
    assert "Portable-*.zip" in combined
    assert "Portable-*.tar.xz" in combined
    assert "Portable-*.dmg" in combined
    playwright_keys = [
        line for line in combined.splitlines() if "key: playwright-" in line
    ]
    assert len(playwright_keys) == 3
    assert all("hashFiles('pyproject.toml')" not in line for line in playwright_keys)


def test_release_builds_use_explicit_supported_runner_generations() -> None:
    windows = _workflow(BUILD_WORKFLOWS["windows"])
    linux = _workflow(BUILD_WORKFLOWS["linux"])
    macos = _workflow(BUILD_WORKFLOWS["macos"])
    assert "runs-on: windows-2025" in windows
    assert "runs-on: ubuntu-22.04" in linux
    assert "runs-on: macos-15" in macos
    assert "runs-on: windows-latest" not in windows
    assert "runs-on: macos-14" not in macos


def test_release_permissions_are_scoped_across_workflow_boundaries() -> None:
    caller = _workflow(CALLER)
    assert "permissions:\n  contents: read" in caller
    assert caller.count("      id-token: write") == 3
    assert caller.count("      attestations: write") == 3
    assert caller.count("      contents: write") == 1

    for filename in BUILD_WORKFLOWS.values():
        workflow = _workflow(filename)
        assert "    permissions:\n      contents: read" in workflow
        assert "      id-token: write" in workflow
        assert "      attestations: write" in workflow

    finalize = _workflow(FINALIZE)
    assert "    permissions:\n      contents: write" in finalize


def test_artifact_contract_connects_builders_to_finalizer() -> None:
    assert "name: artifacts-windows" in _workflow(BUILD_WORKFLOWS["windows"])
    assert "name: artifacts-linux" in _workflow(BUILD_WORKFLOWS["linux"])
    assert "name: artifacts-macos" in _workflow(BUILD_WORKFLOWS["macos"])
    finalize = _workflow(FINALIZE)
    assert "uses: actions/download-artifact@" in finalize
    assert "merge-multiple: true" in finalize
    assert "供应链完整性检查（B14 强制）" in finalize


def test_windows_build_refreshes_verified_asset_caches() -> None:
    script = (PROJECT_ROOT / "build_windows.ps1").read_text(encoding="utf-8")
    assert "function Sync-VerifiedTreeToCache" in script
    assert "Sync-VerifiedTreeToCache $browsersRoot $BrowserCachePath 'Browser'" in script
    assert "Sync-VerifiedTreeToCache $runtimeRoot $RuntimeCachePath 'Runtime asset'" in script
    assert '$backup = "$resolvedDestination.backup"' in script
    assert "Move-Item -LiteralPath $resolvedDestination -Destination $backup" in script
    assert "Move-Item -LiteralPath $backup -Destination $resolvedDestination" in script
