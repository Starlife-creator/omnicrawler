import tempfile
import unittest
from pathlib import Path

from omnicrawl.core.models import CrawlRequest, FetchResult
from omnicrawl.state import StateStore


class StateTest(unittest.TestCase):
    def test_retry_failed_only_requeues_dead_letters(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                failed = CrawlRequest("https://example.org/failed")
                done = CrawlRequest("https://example.org/done")
                state.enqueue(failed)
                state.enqueue(done)
                state.mark_done(failed.fingerprint, status="failed", error="boom")
                state.mark_done(done.fingerprint)

                count = state.retry_failed()
                statuses = {row["url"]: row["status"] for row in state.rows("SELECT url, status FROM frontier")}

                self.assertEqual(count, 1)
                self.assertEqual(statuses[failed.url], "pending")
                self.assertEqual(statuses[done.url], "done")

    def test_frontier_dedup_retry_and_change_detection(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                run = state.start_run("test", "config.yaml")
                request = CrawlRequest("https://example.com/", headers={"Authorization": "Bearer secret", "Accept": "text/html"})
                self.assertTrue(state.enqueue(request))
                stored = state.conn.execute("SELECT headers_json FROM frontier").fetchone()["headers_json"]
                self.assertNotIn("secret", stored)
                self.assertIn("Accept", stored)
                self.assertFalse(state.enqueue(request))
                claimed = state.claim(1)
                self.assertEqual(len(claimed), 1)
                first = FetchResult(request, request.url, 200, {"content-type": "text/html"}, b"one", 0.1)
                self.assertTrue(state.save_response(run, first, None))
                state.mark_done(request.fingerprint)
                state.enqueue(request, force=True)
                second = FetchResult(request, request.url, 200, {"content-type": "text/html"}, b"one", 0.1)
                self.assertFalse(state.save_response(run, second, None))
                third = FetchResult(request, request.url, 200, {"content-type": "text/html"}, b"two", 0.1)
                self.assertTrue(state.save_response(run, third, None))


if __name__ == "__main__":
    unittest.main()
