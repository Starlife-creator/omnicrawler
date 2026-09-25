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
    # 消费者（5）：三个平台构建 + portable-smoke（归档级冒烟，必须用**同一版冻结解释器**）
    # + release（finalize）。★ 新增消费此输出的 job 时，这里要同步 +1 ——
    # 这正是 W4.2 加 portable-smoke 时漏掉的一步（契约用例没跑，CI 才红）。
    assert caller.count("needs.verify-python-version.outputs.build_python_version") == 5
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


def _pinned_uv_version() -> str:
    """★ 单一真源：便携构建/发布工作流用的 uv 版本 = `constraints/quality.txt` 的 `uv==X`。

    这里**不再硬编码版本号字面量**（旧实现写死 `0.12.13`，导致任何 uv 升级都必须
    手改测试断言；2026-09-25 的 dependabot PR 就是这样只改了 constraints，
    把 4 个工作流与 2 条断言落下的）。改为从约束文件派生 ⇒ 升级只需改一处。
    """
    for line in (PROJECT_ROOT / "constraints" / "quality.txt").read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("uv=="):
            return stripped.split("==", 1)[1]
    raise AssertionError("constraints/quality.txt 缺少 `uv==X` 约束行")


def test_portable_builds_install_and_verify_locked_dependencies() -> None:
    scripts = [
        (PROJECT_ROOT / "build_windows.ps1").read_text(encoding="utf-8"),
        (PROJECT_ROOT / "build_linux.sh").read_text(encoding="utf-8"),
        (PROJECT_ROOT / "build_macos.sh").read_text(encoding="utf-8"),
    ]
    for script in scripts:
        assert "sync" in script and "--locked" in script
        assert "check_sbom_lock.py" in script
        assert "pip install -e" not in script

    for filename in (*BUILD_WORKFLOWS.values(), FINALIZE):
        workflow = _workflow(filename)
        assert "uv sync --locked" in workflow
        assert "pip install -e" not in workflow

    constraints = (PROJECT_ROOT / "constraints" / "quality.txt").read_text(encoding="utf-8")
    assert "uv==" in constraints


def test_release_build_toolchain_uv_version_matches_constraints() -> None:
    """★ 版本一致性守卫（防复发）。

    4 个 reusable 工作流里 `setup-uv` 的 `version:` pin 必须与
    `constraints/quality.txt` 的 `uv==X` **逐字相等**。任何一处漏改即全局报红
    ——旧实现只在测试里硬编码一个字面量，只能抓到其中一处，抓不到工作流漂移。
    """
    expected = _pinned_uv_version()
    for filename in (*BUILD_WORKFLOWS.values(), FINALIZE):
        workflow = _workflow(filename)
        assert f'version: "{expected}"' in workflow, (
            f"{filename} 的 setup-uv version pin 与 constraints/quality.txt 的 "
            f"uv=={expected} 不一致（升级 uv 时必须同步此处）"
        )
