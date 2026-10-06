import json

import pytest

from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.fetching.browser_engines import run_actions
from omnicrawler.pipeline._fetch import _PipelineFetch
from omnicrawler.services.workflow_diagnostics import read_runtime
from omnicrawler.state import StateStore


class Engine:
    def locate(self, action):
        return None

    def fill(self, action):
        if action.value == "fail":
            raise ValueError("private error text")

    def wait_ms(self, action):
        pass


def test_actions_record_skips_optional_failure_and_duration_without_values():
    trace = []
    run_actions([
        {"action": "fill", "selector": "PRIVATE-SELECTOR", "value": "PRIVATE-VALUE"},
        {"action": "fill", "if_present": True},
        {"action": "fill", "optional": True, "value": "fail"},
        {"action": "wait_ms", "value": "0"},
    ], Engine(), trace=trace, attempt=2)
    assert [row["status"] for row in trace] == ["succeeded", "skipped_absent", "optional_failed", "succeeded"]
    assert all(row["duration_seconds"] >= 0 and row["finished_at"] >= row["started_at"] for row in trace)
    assert all(row["attempt"] == 2 for row in trace)
    assert trace[2]["error_type"] == "ValueError"
    assert "PRIVATE" not in json.dumps(trace) and "private error" not in json.dumps(trace)


def test_action_trace_bounds_memory_without_skipping_execution():
    trace = []
    engine = Engine()
    calls = []
    engine.fill = lambda action: calls.append(action)
    run_actions([{"action": "fill"}] * 205, engine, trace=trace)
    assert len(calls) == 205
    assert len(trace) == 201
    assert trace[-1]["status"] == "unobserved" and trace[-1]["omitted"] == 5


@pytest.mark.parametrize("failed", [False, True])
def test_production_fetch_keeps_actual_children_even_when_browser_fails(tmp_path, failed):
    path = tmp_path / "task.yaml"
    path.write_text("project: {name: test, task_id: stable, workspace: work}\nsource: {seeds: [https://example.test]}\n")
    config = load_config(path)
    request = CrawlRequest("https://example.test", render=True)
    with StateStore(config.workspace / "state.sqlite3") as state:
        run = state.start_run("test", str(path), task_id="stable")
        pipeline = _PipelineFetch()
        pipeline.state = state

        def fetch(*args):
            run_actions([{"action": "fill", "value": "fail" if failed else "PRIVATE"}], Engine(),
                        trace=request.meta["_browser_action_trace"])
            return FetchResult(request.with_meta_update({"prepared": True}), request.url, 200, {}, b"fixture", 0.1)

        pipeline._fetch_checked_impl = fetch
        if failed:
            with pytest.raises(RuntimeError):
                pipeline._fetch_checked(run, request)
        else:
            result = pipeline._fetch_checked(run, request)
            assert "_browser_action_trace" not in result.request.meta
        report = read_runtime(config, run_id=run)
        child = next(step for step in report["steps"] if step["stage"] == "browser_action")
        parent = next(step for step in report["steps"] if step["stage"] == "fetch")
        assert child["parent_id"] == parent["step_id"]
        assert child["action"] == "fill" and child["index"] == 1
        assert child["status"] == parent["status"] == ("failed" if failed else "succeeded")
        assert "PRIVATE" not in json.dumps(report) and "private error text" not in json.dumps(report)
        assert "_browser_action_trace" not in request.meta
