#!/usr/bin/env python3
"""Reorganize test files into layered subdirectories and strip version suffixes."""

from __future__ import annotations

from pathlib import Path

TESTS = Path(__file__).resolve().parents[1] / "tests"

# Mapping: current filename → target path (relative to tests/)
MOVES = {
    # unit/config
    "test_config_html.py": "unit/config/test_config_html.py",
    "test_migrations.py": "unit/config/test_migrations.py",
    "test_cli_docs_consistency.py": "unit/config/test_cli_docs_consistency.py",
    "test_cli_documented_commands.py": "unit/config/test_cli_documented_commands.py",
    "test_diagnostics_config.py": "unit/config/test_diagnostics_config.py",
    "test_gui_config_preservation.py": "unit/config/test_gui_config_preservation.py",
    # unit/pipeline
    "test_pipeline.py": "unit/pipeline/test_pipeline.py",
    "test_pipeline_security.py": "unit/pipeline/test_pipeline_security.py",
    # unit/state
    "test_state.py": "unit/state/test_state.py",
    "test_state_batch.py": "unit/state/test_state_batch.py",
    # unit/egress
    "test_egress_security.py": "unit/egress/test_egress_security.py",
    "test_egress_v120.py": "unit/egress/test_egress.py",
    # unit/extraction
    "test_api_discovery.py": "unit/extraction/test_api_discovery.py",
    "test_site_inspector.py": "unit/extraction/test_site_inspector.py",
    "test_site_adapters.py": "unit/extraction/test_site_adapters.py",
    "test_field_designer_and_recorder.py": "unit/extraction/test_field_designer.py",
    "test_semantic_and_intelligence.py": "unit/extraction/test_semantic.py",
    "test_research_quality_and_drift.py": "unit/extraction/test_quality_drift.py",
    # unit/template
    "test_template_catalog.py": "unit/template/test_template_catalog.py",
    "test_template_health.py": "unit/template/test_template_health.py",
    # unit/utils
    "test_utils.py": "unit/utils/test_utils.py",
    "test_metrics.py": "unit/utils/test_metrics.py",
    "test_benchmarking.py": "unit/utils/test_benchmarking.py",
    "test_archives.py": "unit/utils/test_archives.py",
    "test_record_sinks.py": "unit/utils/test_record_sinks.py",
    "test_storage_backends.py": "unit/utils/test_storage_backends.py",
    "test_optional_exports.py": "unit/utils/test_optional_exports.py",
    "test_retention.py": "unit/utils/test_retention.py",
    "test_release_checksums.py": "unit/utils/test_release_checksums.py",
    "test_release_integrity.py": "unit/utils/test_release_integrity.py",
    # unit/quality
    "test_quality_review.py": "unit/quality/test_quality_review.py",
    "test_diagnostics_recorder.py": "unit/quality/test_diagnostics_recorder.py",
    # unit/plugin
    "test_plugin.py": "unit/plugin/test_plugin.py",
    "test_plugin_runtime_integration.py": "unit/plugin/test_plugin_runtime.py",
    "test_production.py": "unit/plugin/test_production.py",
    # unit/ai
    "test_ai_task_designer.py": "unit/ai/test_ai_task_designer.py",
    # integration/cli
    "test_cli_workflows_v112.py": "integration/cli/test_cli_workflows.py",
    "test_cli_version.py": "integration/cli/test_cli_version.py",
    "test_v110_features.py": "integration/cli/test_v1_features.py",
    "test_v2_integration.py": "integration/cli/test_v2_integration.py",
    # integration/browser
    "test_browser_contract_v112.py": "integration/browser/test_browser_contract.py",
    "test_browser_integration.py": "integration/browser/test_browser_integration.py",
    "test_http_client_expanded_v112.py": "integration/browser/test_http_client.py",
    "test_strengthened_features.py": "integration/browser/test_strengthened_features.py",
    "test_sources_expanded_v112.py": "integration/browser/test_sources.py",
    # integration/template
    "test_simple_experience_v150.py": "integration/template/test_simple_experience.py",
    "test_help_ux_v150.py": "integration/template/test_help_ux.py",
    "test_operation_enhancements.py": "integration/template/test_operation_enhancements.py",
    # integration/recovery
    "test_recovery_center_v120.py": "integration/recovery/test_recovery_center.py",
    "test_run_reliability_v120.py": "integration/recovery/test_run_reliability.py",
    # integration/sdk
    "test_professional_sdk_v160.py": "integration/sdk/test_professional_sdk.py",
    "test_execution_backend_v140.py": "integration/sdk/test_execution_backend.py",
    "test_worker_task_runner_v140.py": "integration/sdk/test_worker_task_runner.py",
    # integration/archive
    "test_compatibility_v112.py": "integration/archive/test_compatibility.py",
    "test_rc_ecosystem_v190.py": "integration/archive/test_rc_ecosystem.py",
    "test_task_ir_v130.py": "integration/archive/test_task_ir.py",
    "test_architecture_v130.py": "integration/archive/test_architecture.py",
    "test_protocol_runtime_v112.py": "integration/archive/test_protocol_runtime.py",
    "test_runtime_foundations_v112.py": "integration/archive/test_runtime_foundations.py",
    "test_data_value_v170.py": "integration/archive/test_data_value.py",
    "test_adaptive_repair_v180.py": "integration/archive/test_adaptive_repair.py",
    # gui (existing)
    "test_gui_runtime_paths.py": "gui/test_gui_runtime_paths.py",
    "test_gui_smoke.py": "gui/test_gui_smoke.py",
    "test_help_button_v210.py": "gui/test_help_button.py",
    "test_visual_design_v210.py": "gui/test_visual_design.py",
    "test_desktop_interactions_v210.py": "gui/test_desktop_interactions.py",
    # other
    "test_scheduler.py": "unit/other/test_scheduler.py",
    "test_pdfx.py": "unit/other/test_pdfx.py",
    "test_pdfx_cli_review_v112.py": "unit/other/test_pdfx_cli.py",
    "test_workspace_components_v140.py": "unit/other/test_workspace_components.py",
    "test_duckdb_export_security.py": "unit/egress/test_duckdb_export.py",
}


def run():
    created_dirs: set[str] = set()
    moved = 0

    for src_name, dst_rel in sorted(MOVES.items()):
        src = TESTS / src_name
        dst = TESTS / dst_rel

        if not src.is_file():
            print(f"  SKIP (missing) {src_name}")
            continue

        # Create target directory
        dst_dir = dst.parent
        if str(dst_dir) not in created_dirs:
            dst_dir.mkdir(parents=True, exist_ok=True)
            init = dst_dir / "__init__.py"
            if not init.exists():
                init.write_text("")
            created_dirs.add(str(dst_dir))

        # Move file
        src.rename(dst)
        moved += 1
        print(f"  {src_name} → {dst_rel}")

    print(f"\nDone: {moved} files moved, {len(created_dirs)} directories created.")

    # List remaining files in root tests/
    remaining = [p.name for p in TESTS.iterdir() if p.is_file() and p.suffix == ".py" and p.name != "__init__.py"]
    if remaining:
        print(f"\nWARNING: {len(remaining)} files left in tests/ root:")
        for r in sorted(remaining):
            print(f"  - {r}")


if __name__ == "__main__":
    run()
