"""Comprehensive tests for StateStore batch operations.

Covers executemany-based batch paths:
  - save_records (batch insert)
  - add_quality_stats (batch upsert with ON CONFLICT)
  - claim (batch claim with executemany status update)
  - retry_failed with limit
  - track_semantic_changes (batch preload + insert)
  - _preload_versions (N+1 elimination via temp table + LEFT JOIN)
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from omnicrawl.core.models import CrawlRequest, ExtractedRecord, FetchResult
from omnicrawl.state import StateStore


def _make_request(url: str, **kwargs) -> CrawlRequest:
    return CrawlRequest(url, **kwargs)


def _make_record(url: str, rtype: str = "article", data: dict | None = None) -> ExtractedRecord:
    return ExtractedRecord(
        source_url=url,
        record_type=rtype,
        data=data or {"title": f"Record from {url}", "value": 42},
    )


class TestSaveRecordsBatch(unittest.TestCase):
    """save_records uses executemany for batch insert."""

    def test_batch_insert_multiple_records(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                run_id = state.start_run("batch_test", "config.yaml")
                request = _make_request("https://example.com/page1")
                records = [
                    _make_record("https://example.com/page1", data={"title": f"Article {i}", "index": i})
                    for i in range(10)
                ]
                count = state.save_records(run_id, request, records)
                self.assertEqual(count, 10)
                stored = state.rows(
                    "SELECT record_id, data_json FROM records WHERE run_id=? ORDER BY rowid",
                    (run_id,),
                )
                self.assertEqual(len(stored), 10)
                for i, row in enumerate(stored):
                    data = json.loads(row["data_json"])
                    self.assertEqual(data["title"], f"Article {i}")

    def test_batch_insert_empty_list_returns_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                run_id = state.start_run("batch_test", "config.yaml")
                request = _make_request("https://example.com/empty")
                count = state.save_records(run_id, request, [])
                self.assertEqual(count, 0)
                stored = state.rows("SELECT COUNT(*) AS n FROM records WHERE run_id=?", (run_id,))
                self.assertEqual(stored[0]["n"], 0)

    def test_batch_insert_replaces_duplicates(self):
        """save_records uses INSERT OR REPLACE, so re-inserting same IDs replaces."""
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                run_id = state.start_run("batch_test", "config.yaml")
                request = _make_request("https://example.com/replace")
                records = [_make_record("https://example.com/replace", data={"v": 1})]
                state.save_records(run_id, request, records)
                records[0] = _make_record("https://example.com/replace", data={"v": 2})
                state.save_records(run_id, request, records)
                stored = state.rows("SELECT data_json FROM records WHERE run_id=?", (run_id,))
                self.assertEqual(len(stored), 1)
                self.assertEqual(json.loads(stored[0]["data_json"])["v"], 2)


class TestAddQualityStatsBatch(unittest.TestCase):
    """add_quality_stats uses executemany with ON CONFLICT upsert."""

    def test_batch_insert_multiple_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                run_id = state.start_run("quality_test", "config.yaml")
                stats = {
                    "title": {"total": 100, "present": 95, "valid": 90, "anomalies": 5},
                    "date": {"total": 100, "present": 80, "valid": 78, "anomalies": 2},
                    "price": {"total": 100, "present": 50, "valid": 50, "anomalies": 0},
                }
                state.add_quality_stats(run_id, stats)
                result = state.quality_stats(run_id)
                self.assertEqual(len(result), 3)
                field_names = {row["field_name"] for row in result}
                self.assertEqual(field_names, {"title", "date", "price"})

    def test_batch_upsert_accumulates_counts(self):
        """ON CONFLICT DO UPDATE should accumulate counts on re-insert."""
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                run_id = state.start_run("quality_test", "config.yaml")
                stats = {"title": {"total": 10, "present": 8, "valid": 7, "anomalies": 1}}
                state.add_quality_stats(run_id, stats)
                state.add_quality_stats(run_id, stats)
                result = state.quality_stats(run_id)
                self.assertEqual(len(result), 1)
                row = result[0]
                self.assertEqual(row["total"], 20)
                self.assertEqual(row["present"], 16)
                self.assertEqual(row["valid"], 14)
                self.assertEqual(row["anomalies"], 2)

    def test_batch_empty_stats_noop(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                run_id = state.start_run("quality_test", "config.yaml")
                state.add_quality_stats(run_id, {})
                result = state.quality_stats(run_id)
                self.assertEqual(len(result), 0)


class TestClaimBatch(unittest.TestCase):
    """claim uses executemany to batch-update frontier status."""

    def test_batch_claim_multiple_urls(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                for i in range(5):
                    state.enqueue(_make_request(f"https://example.com/page{i}"))
                claimed = state.claim(5)
                self.assertEqual(len(claimed), 5)
                in_progress = state.rows("SELECT url FROM frontier WHERE status='in_progress'")
                self.assertEqual(len(in_progress), 5)
                pending = state.rows("SELECT url FROM frontier WHERE status='pending'")
                self.assertEqual(len(pending), 0)

    def test_batch_claim_bfs_ordering(self):
        """BFS strategy: priority DESC, depth ASC, id ASC."""
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                state.enqueue(_make_request("https://example.com/low", priority=1.0))
                state.enqueue(_make_request("https://example.com/high", priority=10.0))
                state.enqueue(_make_request("https://example.com/mid", priority=5.0))
                claimed = state.claim(3, strategy="bfs")
                self.assertEqual(claimed[0].url, "https://example.com/high")
                self.assertEqual(claimed[1].url, "https://example.com/mid")
                self.assertEqual(claimed[2].url, "https://example.com/low")

    def test_batch_claim_dfs_ordering(self):
        """DFS strategy: depth DESC, priority DESC, id DESC."""
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                state.enqueue(_make_request("https://example.com/shallow", depth=0))
                state.enqueue(_make_request("https://example.com/deep", depth=5))
                state.enqueue(_make_request("https://example.com/mid", depth=2))
                claimed = state.claim(3, strategy="dfs")
                self.assertEqual(claimed[0].url, "https://example.com/deep")
                self.assertEqual(claimed[1].url, "https://example.com/mid")
                self.assertEqual(claimed[2].url, "https://example.com/shallow")

    def test_batch_claim_increments_attempts(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                state.enqueue(_make_request("https://example.com/a"))
                state.enqueue(_make_request("https://example.com/b"))
                state.claim(2)
                attempts = state.rows("SELECT attempts FROM frontier ORDER BY url")
                self.assertTrue(all(int(r["attempts"]) == 1 for r in attempts))

    def test_batch_claim_empty_frontier(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                claimed = state.claim(10)
                self.assertEqual(len(claimed), 0)

    def test_batch_claim_partial_when_limit_exceeds_available(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                state.enqueue(_make_request("https://example.com/a"))
                state.enqueue(_make_request("https://example.com/b"))
                claimed = state.claim(10)
                self.assertEqual(len(claimed), 2)


class TestRetryFailedBatch(unittest.TestCase):
    """retry_failed uses executemany to batch-update frontier entries."""

    def test_batch_retry_with_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                for i in range(10):
                    req = _make_request(f"https://example.com/failed{i}")
                    state.enqueue(req)
                    state.mark_done(req.fingerprint, status="failed", error="timeout")
                count = state.retry_failed(limit=3)
                self.assertEqual(count, 3)
                pending = state.rows("SELECT url FROM frontier WHERE status='pending'")
                self.assertEqual(len(pending), 3)
                still_failed = state.rows("SELECT url FROM frontier WHERE status='failed'")
                self.assertEqual(len(still_failed), 7)

    def test_batch_retry_all_when_no_limit(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                for i in range(5):
                    req = _make_request(f"https://example.com/failed{i}")
                    state.enqueue(req)
                    state.mark_done(req.fingerprint, status="failed", error="500")
                count = state.retry_failed()
                self.assertEqual(count, 5)
                failed = state.rows("SELECT url FROM frontier WHERE status='failed'")
                self.assertEqual(len(failed), 0)

    def test_batch_retry_resets_attempts(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                req = _make_request("https://example.com/retry")
                state.enqueue(req)
                state.claim(1)  # attempts = 1
                state.mark_failed(req, Exception("fail"), max_attempts=1, retryable=False)
                state.retry_failed()
                row = state.rows("SELECT attempts, status FROM frontier WHERE url=?", (req.url,))[0]
                self.assertEqual(row["status"], "pending")
                self.assertEqual(int(row["attempts"]), 0)

    def test_batch_retry_does_not_touch_done(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                failed_req = _make_request("https://example.com/failed")
                done_req = _make_request("https://example.com/done")
                state.enqueue(failed_req)
                state.enqueue(done_req)
                state.mark_done(failed_req.fingerprint, status="failed", error="err")
                state.mark_done(done_req.fingerprint)
                state.retry_failed()
                statuses = {row["url"]: row["status"] for row in state.rows("SELECT url, status FROM frontier")}
                self.assertEqual(statuses[failed_req.url], "pending")
                self.assertEqual(statuses[done_req.url], "done")


class TestTrackSemanticChangesBatch(unittest.TestCase):
    """track_semantic_changes uses _preload_versions (executemany + temp table)."""

    def test_batch_track_new_records(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                run_id = state.start_run("semantic_test", "config.yaml")
                records = [
                    _make_record(
                        f"https://example.com/page{i}",
                        data={"title": f"Title {i}", "body": f"Content {i}"},
                    )
                    for i in range(5)
                ]
                changes = state.track_semantic_changes(run_id, records)
                self.assertEqual(len(changes), 5)
                for change in changes:
                    self.assertEqual(change["change_type"], "added")

    def test_batch_detect_modified_fields(self):
        """Run 1 inserts records, run 2 modifies a field — change should be detected."""
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                run1 = state.start_run("semantic_test", "config.yaml")
                records = [
                    _make_record(
                        f"https://example.com/page{i}",
                        data={"title": f"Title {i}", "price": 100},
                    )
                    for i in range(3)
                ]
                state.track_semantic_changes(run1, records)

                run2 = state.start_run("semantic_test", "config.yaml")
                modified = [
                    _make_record(
                        f"https://example.com/page{i}",
                        data={"title": f"Title {i}", "price": 200},
                    )
                    for i in range(3)
                ]
                changes = state.track_semantic_changes(run2, modified)
                self.assertEqual(len(changes), 3)
                for change in changes:
                    self.assertEqual(change["change_type"], "modified")
                    self.assertIn("price", change.get("modified_fields", {}))

    def test_batch_detect_unchanged_records(self):
        """Re-inserting identical records should produce no changes."""
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                run1 = state.start_run("semantic_test", "config.yaml")
                records = [
                    _make_record(
                        f"https://example.com/page{i}",
                        data={"title": f"Title {i}"},
                    )
                    for i in range(3)
                ]
                state.track_semantic_changes(run1, records)

                run2 = state.start_run("semantic_test", "config.yaml")
                changes = state.track_semantic_changes(run2, records)
                self.assertEqual(len(changes), 0)

    def test_batch_detect_removed_fields(self):
        """Fields present in run 1 but absent in run 2 should be flagged as removed."""
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                run1 = state.start_run("semantic_test", "config.yaml")
                records = [
                    _make_record(
                        "https://example.com/page1",
                        data={"title": "T", "author": "A", "date": "2024"},
                    ),
                ]
                state.track_semantic_changes(run1, records)

                run2 = state.start_run("semantic_test", "config.yaml")
                modified = [
                    _make_record(
                        "https://example.com/page1",
                        data={"title": "T"},  # author and date removed
                    ),
                ]
                changes = state.track_semantic_changes(run2, modified)
                self.assertEqual(len(changes), 1)
                self.assertEqual(changes[0]["change_type"], "modified")
                removed = set(changes[0].get("removed_fields", ()))
                self.assertEqual(removed, {"author", "date"})

    def test_preload_versions_n1_elimination(self):
        """_preload_versions should batch all lookups in a single SQL round-trip.

        We verify correctness: all records from a previous run should be
        found by the preload, and no changes should be reported for
        identical data.
        """
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                run1 = state.start_run("preload_test", "config.yaml")
                records = [
                    _make_record(
                        f"https://example.com/item{i}",
                        rtype="product",
                        data={"name": f"Product {i}", "price": i * 10},
                    )
                    for i in range(20)
                ]
                state.track_semantic_changes(run1, records)

                # Verify record_versions table has all 20 entries
                versions = state.rows("SELECT COUNT(*) AS n FROM record_versions")
                self.assertEqual(int(versions[0]["n"]), 20)

                run2 = state.start_run("preload_test", "config.yaml")
                changes = state.track_semantic_changes(run2, records)
                self.assertEqual(len(changes), 0)  # all unchanged

    def test_batch_track_empty_records(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                run_id = state.start_run("semantic_test", "config.yaml")
                changes = state.track_semantic_changes(run_id, [])
                self.assertEqual(len(changes), 0)


class TestSaveResponseBatch(unittest.TestCase):
    """Verify save_response content versioning across multiple URLs."""

    def test_batch_save_responses_tracks_versions(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                run_id = state.start_run("response_test", "config.yaml")
                for i in range(5):
                    req = _make_request(f"https://example.com/page{i}")
                    result = FetchResult(
                        request=req,
                        final_url=req.url,
                        status=200,
                        headers={"content-type": "text/html"},
                        body=f"content-{i}".encode(),
                        elapsed_seconds=0.1,
                    )
                    changed = state.save_response(run_id, result, None)
                    self.assertTrue(changed)

                responses = state.rows("SELECT COUNT(*) AS n FROM responses WHERE run_id=?", (run_id,))
                self.assertEqual(int(responses[0]["n"]), 5)
                versions = state.rows("SELECT COUNT(*) AS n FROM content_versions")
                self.assertEqual(int(versions[0]["n"]), 5)

    def test_batch_save_responses_detects_unchanged(self):
        """Saving the same content twice should report no change the second time."""
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                run_id = state.start_run("response_test", "config.yaml")
                req = _make_request("https://example.com/same")
                result = FetchResult(
                    request=req,
                    final_url=req.url,
                    status=200,
                    headers={"content-type": "text/html"},
                    body=b"identical",
                    elapsed_seconds=0.1,
                )
                self.assertTrue(state.save_response(run_id, result, None))
                self.assertFalse(state.save_response(run_id, result, None))


class TestExportCommitBatch(unittest.TestCase):
    """Verify export commit idempotency in batch scenarios."""

    def test_batch_export_begin_finish(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                run_id = state.start_run("export_test", "config.yaml")
                exporters = ["csv", "json", "sqlite"]
                for exporter in exporters:
                    key = f"{exporter}-{run_id}"
                    self.assertTrue(state.begin_export(run_id, exporter, key))
                    state.finish_export(key, {"path": f"/tmp/{exporter}"})
                    commit = state.export_commit(key)
                    self.assertIsNotNone(commit)
                    self.assertEqual(commit["status"], "succeeded")

    def test_batch_export_idempotent_begin(self):
        """Re-beginning an already-running export should return False."""
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                run_id = state.start_run("export_test", "config.yaml")
                key = "idempotent-key"
                self.assertTrue(state.begin_export(run_id, "csv", key))
                self.assertFalse(state.begin_export(run_id, "csv", key))


class TestRecoverIncompleteRuns(unittest.TestCase):
    """recover_incomplete_runs batch-recovers crash-interrupted runs."""

    def test_batch_recover_multiple_running_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                for _ in range(3):
                    state.start_run("recover_test", "config.yaml")
                recovered = state.recover_incomplete_runs()
                self.assertEqual(len(recovered), 3)
                for run_id in recovered:
                    row = state.rows("SELECT status FROM runs WHERE run_id=?", (run_id,))[0]
                    self.assertEqual(row["status"], "retrying")

    def test_recover_resets_in_progress_frontier(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                state.start_run("recover_test", "config.yaml")
                state.enqueue(_make_request("https://example.com/a"))
                state.enqueue(_make_request("https://example.com/b"))
                state.claim(2)
                in_progress = state.rows("SELECT url FROM frontier WHERE status='in_progress'")
                self.assertEqual(len(in_progress), 2)
                state.recover_incomplete_runs()
                pending = state.rows("SELECT url FROM frontier WHERE status='pending'")
                self.assertEqual(len(pending), 2)
                in_progress = state.rows("SELECT url FROM frontier WHERE status='in_progress'")
                self.assertEqual(len(in_progress), 0)


if __name__ == "__main__":
    unittest.main()
