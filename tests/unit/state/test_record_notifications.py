import json
import sqlite3
from contextlib import closing

import pytest

from omnicrawler.core.models import ExtractedRecord
from omnicrawler.scheduling.monitor_store import MonitorStore
from omnicrawler.state import StateStore

NOTICE = {"rule_id": "task-rule", "target_id": "webhook:example", "config_sha256": "a" * 64}


def observe(store, value, *, task="task", notification=NOTICE, run=None):
    run = run or store.start_run("Example", "task.yaml", task_id=task)
    changes = store.track_semantic_changes(
        run, [ExtractedRecord("https://example.test/item", "item", {"id": 1, "price": value})],
        identity_fields=("id",), notification=notification)
    return run, changes


def test_record_changes_share_recoverable_delivery_queue_and_reappearance_baseline(tmp_path):
    path = tmp_path / "state.sqlite3"
    with StateStore(path) as store:
        observe(store, 100)
        assert not store.rows("SELECT * FROM target_deliveries")
        observe(store, 80)
        observe(store, 100)
        run, changes = observe(store, 100)
        assert changes == []
        observe(store, 100, run=run)
        observe(store, 80, task="different")
        rows = store.rows("SELECT * FROM target_deliveries")
        assert len(rows) == 2
        payload = json.loads(rows[0]["body_json"])
        assert payload["source_kind"] == "record_fields"
        assert payload["task_key"] == "id:task"
        assert payload["config_sha256"] == NOTICE["config_sha256"]
        assert payload["details"]["before"]["price"] == 100
        assert payload["details"]["after"]["price"] == 80
        assert not store.rows("SELECT * FROM deliveries")  # No invented desktop receipt.
    queue = MonitorStore(path, delivery_only=True)
    rows = queue.pending({NOTICE["rule_id"]}, target=True)
    assert len(rows) == 2
    row = rows[0]
    lease = queue.claim(row["event_id"], target_id=row["target_id"])
    queue.fail(row["event_id"], target_id=row["target_id"], lease_token=lease, error="webhook_failed", delay=0)
    reopened = MonitorStore(path, delivery_only=True)
    retry = reopened.pending({NOTICE["rule_id"]}, target=True)[0]
    assert retry["event_id"] == row["event_id"] and retry["attempts"] == 1
    lease = reopened.claim(row["event_id"], target_id=row["target_id"])
    reopened.acknowledge(row["event_id"], target_id=row["target_id"], lease_token=lease)
    assert all(item["event_id"] != row["event_id"] for item in reopened.pending({NOTICE["rule_id"]}, target=True))


def test_enqueue_failure_rolls_back_change_and_observation_in_same_transaction(tmp_path):
    with StateStore(tmp_path / "state.sqlite3") as store:
        observe(store, 100)
        store.conn.execute("CREATE TRIGGER reject_notice BEFORE INSERT ON target_deliveries BEGIN SELECT RAISE(ABORT,'fixture'); END;")
        store.conn.commit()
        run = store.start_run("Example", "task.yaml", task_id="task")
        with pytest.raises(sqlite3.IntegrityError, match="fixture"):
            observe(store, 80, run=run)
        assert not store.rows("SELECT * FROM semantic_changes WHERE run_id=?", (run,))
        assert not store.rows("SELECT * FROM entity_observations WHERE run_id=?", (run,))
        store.conn.execute("DROP TRIGGER reject_notice")
        store.conn.commit()
        observe(store, 80, run=run)
        assert len(store.rows("SELECT * FROM target_deliveries")) == 1


def test_version_one_upgrade_preserves_observations_and_backup(tmp_path):
    path = tmp_path / "state.sqlite3"
    with StateStore(path) as store:
        observe(store, 100, notification=None)
        store.conn.execute("DROP TABLE target_deliveries")
        store.conn.execute("DROP TABLE deliveries")
        store.conn.execute("DELETE FROM schema_migrations WHERE version=2")
        store.conn.execute("PRAGMA user_version=1")
        store.conn.commit()
    with StateStore(path) as store:
        assert store.conn.execute("PRAGMA user_version").fetchone()[0] == 2
        backup = store.conn.execute("SELECT backup_name FROM schema_migrations WHERE version=2").fetchone()[0]
        observe(store, 80)
        assert len(store.rows("SELECT * FROM target_deliveries")) == 1
    with closing(sqlite3.connect(tmp_path / backup)) as old:
        assert old.execute("PRAGMA user_version").fetchone()[0] == 1
        assert old.execute("SELECT COUNT(*) FROM entity_observations").fetchone()[0] == 1


def test_delivery_adapter_rejects_future_database_without_changes(tmp_path):
    path = tmp_path / "future.sqlite3"
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA user_version=999")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="升级"):
        MonitorStore(path, delivery_only=True).pending({"task-rule"}, target=True)
    assert path.read_bytes() == before
