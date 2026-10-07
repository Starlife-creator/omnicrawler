"""Removal confirmation preserves incompleteness, isolation and transaction boundaries."""
import json
from dataclasses import replace

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest, ExtractedRecord, FetchResult
from omnicrawler.services.record_deletions import finalize_removed
from omnicrawler.state import StateStore

URL = "https://example.test/items"


def setup(tmp_path):
    path = tmp_path / "task.yaml"
    path.write_text(yaml.safe_dump({"project": {"name": "Task", "task_id": "stable"},
                                  "source": {"seeds": [URL]},
                                  "updates": {"identity_fields": ["id"], "notifications": {
                                      "enabled": True, "include_removed": True, "webhook_url": "https://example.test/hook"}}}), encoding="utf8")
    return load_config(path)


def cycle(state, *, empty=False, status="succeeded", scope="scope", summary=None, task="stable", code=200):
    run = state.start_run("Task", "task.yaml", task_id=task)
    state.save_checkpoint(run, "setup", "setup", {"comparison_scope": scope, "semantic_settings": {"identity_fields": ["id"]}})
    response = FetchResult(CrawlRequest(URL), URL, code, {}, b"[]" if empty else b"data", 0)
    records = [] if empty else [ExtractedRecord(URL, "item", {"id": 1, "price": 100})]
    state.save_response(run, response, None)
    state.save_record_observation(run, response, records)
    state.track_semantic_changes(run, records, identity_fields=("id",))
    if status != "running":
        state.finish_run(run, status, summary or {})
    return run


@pytest.mark.parametrize("condition,reason", [
    ("running", "run_incomplete"), ("scope", "scope_changed_or_unknown"),
    ("budget", "collection_incomplete"), ("conditional", "conditional_response_coverage_unverified"),
])
def test_incomplete_or_changed_scope_cannot_emit_removal(tmp_path, condition, reason):
    config = setup(tmp_path)
    with StateStore(tmp_path / "state.sqlite3") as state:
        cycle(state)
        after = cycle(state, empty=True, status="running" if condition == "running" else "succeeded",
                      scope="other" if condition == "scope" else "scope", code=304 if condition == "conditional" else 200,
                      summary={"export": {"delivery": {"budget_exhausted": True}}} if condition == "budget" else {})
        result = finalize_removed(config, state, after)
        assert result["removed"] == 0 and reason in result["reasons"]
        assert state.rows("SELECT * FROM target_deliveries") == []
        assert state.rows("SELECT * FROM stage_checkpoints WHERE stage='record_deletion'") == []


def test_confirmed_removal_is_idempotent_and_transactional(tmp_path, monkeypatch):
    config = setup(tmp_path)
    with StateStore(tmp_path / "state.sqlite3") as state:
        cycle(state)
        after = cycle(state, empty=True)
        import omnicrawler.services.record_deletions as service
        original = service.enqueue_event
        def failing(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("injected interruption")
        monkeypatch.setattr(service, "enqueue_event", failing)
        with pytest.raises(RuntimeError, match="interruption"):
            finalize_removed(config, state, after)
        assert state.rows("SELECT * FROM target_deliveries") == []
        assert state.rows("SELECT * FROM stage_checkpoints WHERE stage IN ('record_deletion','record_removal')") == []
        monkeypatch.setattr(service, "enqueue_event", original)
        result = finalize_removed(config, state, after)
        assert result["removed"] == 1
        assert finalize_removed(config, state, after) == result
        assert len(state.rows("SELECT * FROM target_deliveries")) == 1
        assert len(state.rows("SELECT * FROM semantic_changes WHERE change_type='removed'")) == 1
        returned = cycle(state)
        changes = state.rows("SELECT before_json,change_type FROM semantic_changes WHERE run_id=?", (returned,))
        assert changes == [{"before_json": None, "change_type": "added"}]
        assert json.loads(state.rows("SELECT data_json FROM entity_observations WHERE run_id=?", (returned,))[0]["data_json"])["price"] == 100


def test_other_task_tombstone_and_disabled_notice_do_not_affect_presence(tmp_path):
    config = setup(tmp_path)
    with StateStore(tmp_path / "state.sqlite3") as state:
        first = cycle(state)
        assert finalize_removed(config, state, first)["reasons"] == ["baseline"]
        after = cycle(state, empty=True)
        assert finalize_removed(config, state, after)["removed"] == 1
        other = cycle(state, task="other")
        assert state.rows("SELECT change_type FROM semantic_changes WHERE run_id=?", (other,)) == [{"change_type": "added"}]
        disabled = replace(config, raw={**config.raw, "updates": {"notifications": {"enabled": False}}})
        assert finalize_removed(disabled, state, other)["reasons"] == ["disabled"]
