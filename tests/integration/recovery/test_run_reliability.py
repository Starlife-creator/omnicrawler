from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawl.core.config import load_config
from omnicrawl.core.models import CrawlRequest
from omnicrawl.core.run_state import ALLOWED_TRANSITIONS, RUN_STATES, canonical_run_state, require_transition
from omnicrawl.pipeline import Pipeline
from omnicrawl.state import StateStore


def test_run_state_machine_accepts_only_declared_transitions() -> None:
    assert canonical_run_state("completed") == "succeeded"
    assert canonical_run_state("stopped") == "cancelled"
    with pytest.raises(ValueError, match="未知任务状态"):
        canonical_run_state("mystery")
    for current in RUN_STATES:
        for target in RUN_STATES:
            if target == current or target in ALLOWED_TRANSITIONS[current]:
                assert require_transition(current, target) == (current, target)
            else:
                with pytest.raises(ValueError, match="非法任务状态转换"):
                    require_transition(current, target)


def test_state_events_checkpoints_and_export_commits_are_idempotent(tmp_path: Path) -> None:
    with StateStore(tmp_path / "state.sqlite3") as state:
        run_id = state.start_run("demo", "task.yaml")
        state.transition_run(run_id, "paused", reason="test")
        state.transition_run(run_id, "running", reason="test")
        state.save_checkpoint(run_id, "fetch", "abc", {"page": 1})
        state.save_checkpoint(run_id, "fetch", "abc", {"page": 2})
        checkpoint = state.checkpoint(run_id, "fetch", "abc")
        assert checkpoint is not None and checkpoint["payload"] == {"page": 2}

        assert state.begin_export(run_id, "demo", "stable-key") is True
        assert state.begin_export(run_id, "demo", "stable-key") is False
        state.finish_export("stable-key", {"path": "result.json"})
        assert state.begin_export(run_id, "demo", "stable-key") is False
        assert state.export_commit("stable-key")["result"] == {"path": "result.json"}
        state.finish_run(run_id, "succeeded", {"status": "succeeded"})

        events = state.rows(
            "SELECT from_state, to_state FROM run_state_events WHERE run_id=? ORDER BY id",
            (run_id,),
        )
        assert [(row["from_state"], row["to_state"]) for row in events] == [
            ("pending", "running"),
            ("running", "paused"),
            ("paused", "running"),
            ("running", "succeeded"),
        ]
        with pytest.raises(ValueError, match="非法任务状态转换"):
            state.transition_run(run_id, "running")


def test_crash_recovery_requeues_claims_and_export_without_duplicate_commit(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    first = StateStore(database)
    run_id = first.start_run("demo", "task.yaml")
    request = CrawlRequest("https://example.org/item")
    first.enqueue(request)
    assert first.claim(1)[0].fingerprint == request.fingerprint
    assert first.begin_export(run_id, "remote", "remote-key") is True
    first.close()

    with StateStore(database) as recovered:
        assert recovered.recover_incomplete_runs() == [run_id]
        assert recovered.latest_run()["status"] == "retrying"
        assert recovered.stats()["frontier"]["pending"] == 1
        assert recovered.export_commit("remote-key")["status"] == "retrying"
        recovered.transition_run(run_id, "running", reason="resume")
        assert recovered.begin_export(run_id, "remote", "remote-key") is True
        recovered.finish_export("remote-key", {"remote_id": "one"})
        recovered.finish_run(run_id, "succeeded", {"status": "succeeded"})


def test_pipeline_exporter_is_committed_once_per_run(tmp_path: Path) -> None:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: once, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        "http: {respect_robots: false}\n"
        "outputs: {exporter: once, jsonl: false, csv: false, xlsx: false}\n",
        encoding="utf-8",
    )
    calls: list[str] = []
    with Pipeline(load_config(config_path)) as pipeline:
        pipeline.registry.register_exporter(
            "once", lambda _config, _state, run_id, _options: calls.append(run_id) or {"ok": True}
        )
        run_id = pipeline.state.start_run("once", str(config_path))
        assert pipeline._run_exports(run_id)["ok"] is True
        assert pipeline._run_exports(run_id)["ok"] is True
        assert calls == [run_id]
        pipeline.state.finish_run(run_id, "succeeded", {"status": "succeeded"})
