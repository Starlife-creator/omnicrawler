"""OmniCrawler repository engineering & verification tooling package."""

from __future__ import annotations

import importlib
import sys
from importlib.abc import MetaPathFinder
from importlib.machinery import ModuleSpec
from types import ModuleType
from typing import Any

SUBPACKAGE_BY_MODULE: dict[str, str] = {
    "audit_paper_scenario": "qa",
    "benchmark_adaptive_arms": "benchmarks",
    "benchmark_convertx": "benchmarks",
    "benchmark_core": "benchmarks",
    "benchmark_pdf_quality": "benchmarks",
    "benchmark_quality": "benchmarks",
    "build_common": "builders",
    "build_source_archive": "builders",
    "build_update_delta": "builders",
    "build_update_manifest": "builders",
    "build_wheel": "builders",
    "bump_version": "releasing",
    "check_agent_surface": "gates",
    "check_anchor_integrity": "gates",
    "check_architecture": "gates",
    "check_artifact_budget": "gates",
    "check_build_profiles": "gates",
    "check_cli_docs": "gates",
    "check_coding_standards": "gates",
    "check_coverage_gates": "gates",
    "check_dependency_consistency": "gates",
    "check_doc_numbers": "gates",
    "check_docs_consistency": "gates",
    "check_extra_install": "gates",
    "check_extras_expansion": "gates",
    "check_gitignore_hygiene": "gates",
    "check_guardrails": "gates",
    "check_gui_conventions": "gates",
    "check_gui_state_threading": "gates",
    "check_gui_test_layout": "gates",
    "check_line_endings_policy": "gates",
    "check_lint_budget": "gates",
    "check_linux_delivery": "gates",
    "check_lockfile_consistency": "gates",
    "check_main_window_size": "gates",
    "check_market_content": "gates",
    "check_minimal_install": "gates",
    "check_network_boundaries": "gates",
    "check_portable_doc_manifest": "gates",
    "check_release_integrity": "gates",
    "check_sbom_lock": "gates",
    "check_scenario_cards": "gates",
    "check_sdk_api": "gates",
    "check_shell_scripts": "gates",
    "check_silent_exception_handlers": "gates",
    "check_subprocess_encoding": "gates",
    "check_visual_regression": "gates",
    "checkout_market": "market",
    "ci_coverage_pytest": "qa",
    "compile_i18n": "i18n",
    "compile_mo": "i18n",
    "create_runtime_manifest": "builders",
    "create_zip": "builders",
    "download_and_smoke_test": "releasing",
    "extract_i18n": "i18n",
    "fix_macos_bundle_openssl": "builders",
    "generate_checksums": "releasing",
    "generate_en_po": "i18n",
    "generate_provenance": "releasing",
    "generate_release_info": "releasing",
    "generate_sbom": "releasing",
    "i18n_stats": "i18n",
    "identity": "signing",
    "manifest_common": "builders",
    "market": "market",
    "measure_optimization_baseline": "benchmarks",
    "offline_acceptance": "qa",
    "portable_archive_smoke": "portable",
    "portable_smoke_test": "portable",
    "portable_upgrade_smoke": "portable",
    "post_bump_steps": "releasing",
    "public_retest": "qa",
    "rebase_venv": "portable",
    "refresh_doc_versions": "releasing",
    "release_delta_baselines": "releasing",
    "reorganize_tests": "qa",
    "ressign_market": "signing",
    "sign_component": "signing",
    "sign_plugin": "signing",
    "sign_plugins_batch": "signing",
    "sign_update_manifest": "signing",
    "sync_snapshot": "market",
    "walkthrough_demo_site": "qa",
    "walkthrough_env": "qa",
}


class _ToolsAliasFinder(MetaPathFinder):
    """Allow legacy `import tools.<name>` to resolve canonical `tools.<sub>.<name>`."""

    def find_spec(
        self,
        fullname: str,
        path: Any = None,
        target: ModuleType | None = None,
    ) -> ModuleSpec | None:
        if not fullname.startswith("tools."):
            return None
        rest = fullname.removeprefix("tools.")
        if "." in rest:
            return None
        sub = SUBPACKAGE_BY_MODULE.get(rest)
        if sub is None or sub == rest:
            return None
        canonical = f"tools.{sub}.{rest}"
        mod = importlib.import_module(canonical)
        sys.modules[fullname] = mod
        return mod.__spec__


if not any(type(f).__name__ == '_ToolsAliasFinder' for f in sys.meta_path):
    sys.meta_path.insert(0, _ToolsAliasFinder())


def __getattr__(name: str) -> Any:
    sub = SUBPACKAGE_BY_MODULE.get(name)
    if sub is not None:
        mod = importlib.import_module(f"tools.{sub}.{name}")
        globals()[name] = mod
        return mod
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
