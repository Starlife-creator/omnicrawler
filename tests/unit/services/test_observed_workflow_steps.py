import json

import pytest

from omnicrawler.core.config import load_config
from omnicrawler.services.workflow_diagnostics import read_runtime
from omnicrawler.state import StateStore


def test_step_boundaries_survive_failure_and_only_include_safe_summaries(tmp_path):
    path = tmp_path / "task.yaml"
    path.write_text("project: {name: test, task_id: stable, workspace: work}\nsource: {seeds: [https://example.com]}\n")
    config = load_config(path)
    with StateStore(config.workspace / "state.sqlite3") as state:
        run = state.start_run("test", str(path), task_id="stable")
        with state.observed_step(run, "fetch", "request") as result:
            result.update(response_bytes=10, http_status=200, cookie="SECRET")
        with pytest.raises(ValueError), state.observed_step(run, "extract", "request", parent_id="fetch:request"):
            raise ValueError("SECRET")
        report = read_runtime(config, run_id=run)
        fetch = next(step for step in report["steps"] if step["stage"] == "fetch")
        extract = next(step for step in report["steps"] if step["stage"] == "extract")
        assert fetch["started_at"] and fetch["finished_at"]
        assert fetch["duration_seconds"] >= 0
        assert fetch["response_bytes"] == 10
        assert extract["status"] == "failed"
        assert extract["error_type"] == "ValueError"
        assert extract["parent_id"] == fetch["step_id"]
        assert "SECRET" not in json.dumps(report)


def test_pipeline_records_actual_fetch_and_processor_boundaries(tmp_path, monkeypatch):
    from omnicrawler.core.models import FetchResult
    from omnicrawler.pipeline import Pipeline

    path = tmp_path / "task.yaml"
    path.write_text("project: {name: steps, workspace: work}\nsource: {seeds: [https://example.com]}\n"
                    "extract: {mode: html, fields: {title: {selector: h1}}}\noutputs: {jsonl: true, csv: false, xlsx: false}\n")
    config = load_config(path)
    body = b"<html><h1>Verified</h1></html>"
    monkeypatch.setattr(Pipeline, "_fetch_checked_impl", lambda self, run, request: FetchResult(
        request, request.url, 200, {"content-type": "text/html"}, body, 0.01))
    with Pipeline(config) as pipeline:
        result = pipeline.run(max_pages=1)
        report = read_runtime(config, run_id=result["run_id"])
    assert result["status"] == "succeeded"
    fetch = next(step for step in report["steps"] if step["stage"] == "fetch")
    extract = next(step for step in report["steps"] if step["stage"] == "extract")
    assert fetch["response_bytes"] == len(body)
    assert extract["records"] == 1
    assert extract["parent_id"] == fetch["step_id"]
