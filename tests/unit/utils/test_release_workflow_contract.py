from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def test_release_preflight_runs_before_platform_builds() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    preflight = workflow.index("发布前快速门禁")
    windows_build = workflow.index("build-windows-portable:")
    assert preflight < windows_build
    for command in (
        "python -m compileall -q src tests examples tools",
        "python -m ruff check src tests tools",
        "python tools/check_release_integrity.py",
        "python tools/check_architecture.py",
        "python tools/check_network_boundaries.py --source-root src/omnicrawler",
    ):
        assert command in workflow


def test_manual_dispatch_cannot_publish_a_branch_as_a_release() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch:" in workflow
    assert "if: github.event_name == 'push'\n        uses: softprops/action-gh-release@" in workflow
    assert "if: github.event_name == 'push'\n        env:\n          GH_TOKEN:" in workflow


def test_all_portable_platforms_receive_attestations_and_stable_cache_keys() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert workflow.count("uses: actions/attest@") == 3
    assert "Portable-*.zip" in workflow
    assert "Portable-*.tar.xz" in workflow
    assert "Portable-*.dmg" in workflow
    playwright_keys = [line for line in workflow.splitlines() if "key: playwright-" in line]
    assert len(playwright_keys) == 3
    assert all("hashFiles('pyproject.toml')" not in line for line in playwright_keys)


def test_release_permissions_are_scoped_to_the_jobs_that_need_them() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert "permissions:\n  contents: read" in workflow
    assert workflow.count("      id-token: write") == 3
    assert workflow.count("      attestations: write") == 3
    assert workflow.count("      contents: write") == 1
    release_job = workflow[workflow.index("  release:") :]
    assert "    permissions:\n      contents: write" in release_job


def test_windows_build_refreshes_verified_asset_caches() -> None:
    script = (PROJECT_ROOT / "build_windows.ps1").read_text(encoding="utf-8")
    assert "function Sync-VerifiedTreeToCache" in script
    assert "Sync-VerifiedTreeToCache $browsersRoot $BrowserCachePath 'Browser'" in script
    assert "Sync-VerifiedTreeToCache $runtimeRoot $RuntimeCachePath 'Runtime asset'" in script
    assert '$backup = "$resolvedDestination.backup"' in script
    assert "Move-Item -LiteralPath $resolvedDestination -Destination $backup" in script
    assert "Move-Item -LiteralPath $backup -Destination $resolvedDestination" in script
