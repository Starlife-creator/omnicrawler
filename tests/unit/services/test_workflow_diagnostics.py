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
