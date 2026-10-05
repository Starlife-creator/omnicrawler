from __future__ import annotations

import json

from omnicrawler.core.config import load_config
from omnicrawler.services.workflow_diagnostics import describe
from omnicrawler.templates.capture import config_digest


def test_stages_include_actions_pagination_attachments_without_secret_values(tmp_path):
    path = tmp_path / "task.yaml"
    path.write_text("project: {name: stages, workspace: work}\nsource: {kind: browser, seeds: [https://example.org], pagination: {type: page, parameter: page, end: 2}}\nbrowser: {actions: [{action: fill, selector: '#query', value: SECRET}]}\ndownload: {enabled: true}\n", encoding="utf-8")
    config = load_config(path)
    report = describe(config)
    assert {step["id"] for step in report["stages"]} >= {"action-1", "pagination", "download", "export"}
    assert "SECRET" not in json.dumps(report)
    assert report["trial"]["state"] == "missing"
    config.workspace.mkdir(parents=True)
    proof = config.workspace / "preflight_acceptance.json"
    proof.write_text(json.dumps({"config_sha256": config_digest(config), "samples": [1], "summary": {"status": "succeeded"}}))
    assert describe(config)["trial"]["state"] == "matching_history"
    config.raw["source"]["seeds"] = ["https://example.org/new"]
    assert describe(config)["trial"]["state"] == "stale_or_incomplete"


def test_runtime_is_bound_to_task_and_reports_real_failures_without_secrets(tmp_path):
    from omnicrawler.state import StateStore
    path = tmp_path / "task.yaml"
    path.write_text("project: {name: stages, task_id: chosen, workspace: work}\nsource: {kind: static_html, seeds: [https://example.org]}\n", encoding="utf-8")
    config = load_config(path)
    config.workspace.mkdir(parents=True)
    assert describe(config)["runtime"]["state"] == "not_started"
    with StateStore(config.workspace / "state.sqlite3") as state:
        run = state.start_run("stages", str(path), task_id="chosen")
        state.save_checkpoint(run, "fetch", "request", {"cookie": "SECRET"})
        state.add_error(run, None, "extract", ValueError("SECRET"), retryable=False)
        other = state.start_run("other", str(path), task_id="other")
        state.save_checkpoint(other, "export", "unrelated", {})
    report = describe(config)
    assert report["runtime"]["run_id"] == run
    assert "SECRET" not in json.dumps(report)
    assert next(s for s in report["runtime"]["stages"] if s["stage"] == "extract")["status"] == "failed"
    assert next(s for s in report["stages"] if s["id"] == "export")["runtime_status"] == "not_observed"
