"""Notification noise controls preserve observations and survive restarts."""
import json

from omnicrawler.core.models import ExtractedRecord
from omnicrawler.state import StateStore


def _observe(store, price, policy, *, run=None, task="task"):
    run = run or store.start_run("Products", "task.yaml", task_id=task)
    notice = {"rule_id": task, "target_id": "webhook:test", "config_sha256": "a" * 64, "policy": policy}
    changes = store.track_semantic_changes(
        run, [ExtractedRecord("https://example.test/item", "item", {"id": 1, "price": price})],
        identity_fields=("id",), notification=notice,
    )
    return run, changes


def _pending(store):
    return [json.loads(row["body_json"]) for row in store.rows(
        "SELECT body_json FROM target_deliveries WHERE status='pending' ORDER BY rowid")]


def test_small_changes_accumulate_against_last_enqueued_value(tmp_path):
    policy = {"fields": {"price": {"minimum_absolute_change": 10}}}
    with StateStore(tmp_path / "state.sqlite3") as store:
        _observe(store, 100, policy)
        _observe(store, 99, policy)
        _observe(store, 95, policy)
        assert _pending(store) == []
        _observe(store, 89, policy)
        notice = _pending(store)[0]
        assert notice["details"]["before"]["price"] == 100
        assert notice["details"]["after"]["price"] == 89
        assert len(store.rows("SELECT * FROM entity_observations")) == 4
        assert len(store.rows("SELECT * FROM semantic_changes WHERE baseline=0")) == 3


def test_confirmation_requires_distinct_observation_runs_after_reopen(tmp_path):
    policy = {"confirmations": 2}
    path = tmp_path / "state.sqlite3"
    with StateStore(path) as store:
        _observe(store, 100, policy)
        run, _ = _observe(store, 80, policy)
        _observe(store, 80, policy, run=run)
        assert _pending(store) == []
    with StateStore(path) as store:
        _observe(store, 80, policy)
        assert len(_pending(store)) == 1
        _observe(store, 80, policy)
        assert len(_pending(store)) == 1


def test_cooldown_merges_latest_difference_without_losing_facts(tmp_path, monkeypatch):
    from omnicrawler.state import state_store_records

    policy = {"cooldown_seconds": 60}
    clock = ["2026-10-06T00:00:00+00:00"]
    monkeypatch.setattr(state_store_records, "utcnow", lambda: clock[0])
    with StateStore(tmp_path / "state.sqlite3") as store:
        _observe(store, 100, policy)
        _observe(store, 80, policy)
        clock[0] = "2026-10-06T00:00:10+00:00"
        _observe(store, 70, policy)
        assert len(_pending(store)) == 1
        clock[0] = "2026-10-06T00:01:01+00:00"
        _observe(store, 70, policy)
        assert len(_pending(store)) == 2
        assert _pending(store)[-1]["details"]["before"]["price"] == 80
        assert _pending(store)[-1]["details"]["after"]["price"] == 70
        assert len(store.rows("SELECT * FROM entity_observations")) == 4


def test_relative_zero_baseline_is_explicitly_suppressed(tmp_path):
    policy = {"fields": {"price": {"minimum_relative_change": 0.1}}}
    with StateStore(tmp_path / "state.sqlite3") as store:
        _observe(store, 0, policy)
        _observe(store, 5, policy)
        assert _pending(store) == []
        row = store.rows("SELECT status,body_json FROM target_deliveries")[0]
        assert row["status"] == "suppressed"
        assert json.loads(row["body_json"])["suppression_reason"] == "relative_baseline_zero"
