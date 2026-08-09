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

    def test_s253_claim_conditional_update_no_double_claim(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                first = CrawlRequest("https://example.org/a")
                second = CrawlRequest("https://example.org/b")
                state.enqueue(first)
                state.enqueue(second)
                # 模拟并发进程已把 first 抢走（in_progress）
                state.conn.execute(
                    "UPDATE frontier SET status='in_progress' WHERE fingerprint=?",
                    (first.fingerprint,),
                )
                claimed = state.claim(2)
                self.assertEqual([req.url for req in claimed], [second.url])
                # attempts 只在成功认领时 +1
                attempts = {
                    row["url"]: row["attempts"]
                    for row in state.rows("SELECT url, attempts FROM frontier")
                }
                self.assertEqual(attempts[second.url], 1)

    def test_s253_claim_refills_when_candidates_are_stolen(self):
        with tempfile.TemporaryDirectory() as temp:
            with StateStore(Path(temp) / "state.sqlite3") as state:
                state.enqueue(CrawlRequest("https://example.org/x1"))
                state.enqueue(CrawlRequest("https://example.org/x2"))
                state.enqueue(CrawlRequest("https://example.org/x3"))
                # 首轮候选被全部抢走，循环重取剩余 pending
                state.conn.execute("UPDATE frontier SET status='in_progress'")
                state.conn.execute("UPDATE frontier SET status='pending' WHERE url='https://example.org/x3'")
                claimed = state.claim(2)
                self.assertEqual([req.url for req in claimed], ["https://example.org/x3"])


if __name__ == "__main__":
    unittest.main()
