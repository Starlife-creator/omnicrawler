"""Legacy and cache boundaries preserve observed facts and reject unsafe reuse."""
import json
from dataclasses import replace

import pytest

from omnicrawler.core.models import CrawlRequest, ExtractedRecord, FetchResult
from omnicrawler.state import StateStore

URL = "https://example.test/item"


def test_legacy_versions_remain_a_baseline_without_reconstructed_identity(tmp_path):
    with StateStore(tmp_path / "state.sqlite3") as state:
        first = state.start_run("Legacy", "old.yaml")
        old = [ExtractedRecord(URL, "item", {"id": 1, "price": 100})]
        state.track_semantic_changes(first, old, identity_fields=("id",))
        state.save_records(first, CrawlRequest(URL), old)
        second = state.start_run("Legacy", "new.yaml")
        state.conn.execute("DELETE FROM entity_observations")
        state.conn.execute("DELETE FROM run_identities")
        state.conn.commit()
        changes = state.track_semantic_changes(second, [ExtractedRecord(URL, "item", {"id": 1, "price": 80})], identity_fields=("id",))
        assert len(changes) == 1 and changes[0]["change_type"] == "modified"
        assert changes[0]["before"]["price"] == 100 and not changes[0]["baseline"]
        assert state._preload_versions(second, []) == {}
        with pytest.raises(KeyError, match="运行不存在"):
            state._observation_context("missing")


def test_conflicting_same_cycle_values_roll_back_instead_of_overwriting(tmp_path):
    with StateStore(tmp_path / "state.sqlite3") as state:
        run = state.start_run("Task", "task.yaml", task_id="stable")
        state.track_semantic_changes(run, [ExtractedRecord(URL, "item", {"id": 1, "price": 0})], identity_fields=("id",))
        with pytest.raises(ValueError, match="冲突"):
            state.track_semantic_changes(run, [ExtractedRecord(URL, "item", {"id": 1, "price": 80})], identity_fields=("id",))
        rows = state.rows("SELECT data_json FROM entity_observations WHERE run_id=?", (run,))
        assert len(rows) == 1 and json.loads(rows[0]["data_json"])["price"] == 0


@pytest.mark.parametrize("alteration", ["body", "final_url"])
def test_reuse_rejects_changed_content_or_redirected_snapshot(tmp_path, alteration):
    with StateStore(tmp_path / "state.sqlite3") as state:
        first = state.start_run("Task", "task.yaml", task_id="stable")
        result = FetchResult(CrawlRequest(URL), URL, 200, {"content-type": "application/json"}, b"old", 0)
        state.save_checkpoint(first, "setup", "setup", {"comparison_scope": "scope"})
        state.save_record_observation(first, result, [ExtractedRecord(URL, "item", {"id": 1})])
        second = state.start_run("Task", "task.yaml", task_id="stable")
        assert not state.reuse_record_observation(second, result)  # No comparable setup yet.
        state.save_checkpoint(second, "setup", "setup", {"comparison_scope": "scope"})
        changed = replace(result, body=b"new") if alteration == "body" else replace(result, final_url=URL + "/redirect")
        assert not state.reuse_record_observation(second, changed)
        assert state.checkpoint(second, "record_observation", result.request.fingerprint) is None
