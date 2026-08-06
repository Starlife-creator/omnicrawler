import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from omnicrawl.core.config import load_config
from omnicrawl.pipeline import Pipeline


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/index":
            body = b"<html><title>Index</title><h1>Home</h1><a href='/page2'>Next</a><a href='/report.pdf'>PDF</a></html>"
            kind = "text/html; charset=utf-8"
        elif self.path == "/page2":
            body = b"<html><title>Second</title><h1>Page 2</h1></html>"
            kind = "text/html; charset=utf-8"
        elif self.path == "/report.pdf":
            body = b"%PDF-1.4\n%offline-test\n%%EOF"
            kind = "application/pdf"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


class PipelineTest(unittest.TestCase):
    def test_offline_crawl_attachment_export_and_incremental(self):
        pytest.importorskip("openpyxl")
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                config_path = root / "project.yaml"
                workspace = root / "work"
                config_path.write_text(yaml.safe_dump({
                    "project": {"name": "offline", "workspace": str(workspace)},
                    "source": {"kind": "incremental", "seeds": [f"http://127.0.0.1:{server.server_port}/index"]},
                    "crawl": {"max_pages": 10, "max_depth": 2, "same_host": True, "concurrency": 2},
                    "http": {
                        "user_agent": "OfflineTest/1.0 (+contact: test@example.org)",
                        "respect_robots": False, "delay_seconds": 0, "allow_private_network": True,
                    },
                    "download": {"enabled": True, "extensions": [".pdf"], "media": False},
                    "extract": {"mode": "html", "fields": {"title": {"selector": "title"}, "heading": {"selector": "h1"}}},
                    "outputs": {"jsonl": True, "csv": True, "xlsx": True},
                }, sort_keys=False), encoding="utf-8")
                config = load_config(config_path)
                with Pipeline(config) as pipeline:
                    first = pipeline.run()
                self.assertEqual(first["processed"], 3)
                self.assertEqual(first["records"], 2)
                self.assertEqual(first["artifacts"], 1)
                self.assertTrue(any((workspace / "artifacts" / "pdf").glob("*.pdf")))
                self.assertTrue((workspace / "output" / "records.csv").exists())
                self.assertTrue((workspace / "output" / "extraction_results.xlsx").exists())
                with Pipeline(config) as pipeline:
                    second = pipeline.run()
                self.assertEqual(second["processed"], 3)
                self.assertEqual(second["records"], 0)
                server.shutdown()
                with Pipeline(config) as pipeline:
                    reprocessed = pipeline.reprocess_records(first["run_id"])
                    audit = pipeline.state.rows(
                        "SELECT action FROM audit_events WHERE run_id=? ORDER BY id",
                        (first["run_id"],),
                    )
                self.assertEqual(reprocessed["reprocessed_responses"], 2)
                self.assertEqual(reprocessed["records"], 2)
                self.assertEqual(
                    [row["action"] for row in audit],
                    ["reprocess_records_started", "reprocess_records_finished"],
                )
        finally:
            server.shutdown()
            server.server_close()

    def test_exception_mid_loop_drains_in_progress_rows(self):
        """S1.2.1：循环中途抛异常时，在途请求仍被 drain，frontier 不残留 in_progress。"""
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                workspace = root / "work"
                config_path = root / "project.yaml"
                config_path.write_text(yaml.safe_dump({
                    "project": {"name": "drain", "workspace": str(workspace)},
                    "source": {"kind": "crawl", "seeds": [
                        f"http://127.0.0.1:{server.server_port}/index",
                        f"http://127.0.0.1:{server.server_port}/page2",
                    ]},
                    "crawl": {"max_pages": 2, "max_depth": 1, "same_host": True, "concurrency": 2},
                    "http": {
                        "user_agent": "DrainTest/1.0 (+contact: test@example.org)",
                        "respect_robots": False, "delay_seconds": 0, "allow_private_network": True,
                    },
                    "extract": {"mode": "html", "fields": {"title": {"selector": "title"}}},
                }, sort_keys=False), encoding="utf-8")
                config = load_config(config_path)
                from unittest import mock
                with Pipeline(config) as pipeline:
                    original = pipeline.run_control.wait_if_paused
                    calls = {"n": 0}

                    def _explode(*args, **kwargs):
                        calls["n"] += 1
                        if calls["n"] >= 2:
                            raise RuntimeError("boom")
                        return original(*args, **kwargs)

                    with mock.patch.object(pipeline.run_control, "wait_if_paused", side_effect=_explode):
                        with pytest.raises(RuntimeError, match="boom"):
                            pipeline.run()
                    leftover = pipeline.state.rows(
                        "SELECT status, COUNT(*) AS n FROM frontier GROUP BY status"
                    )
                    statuses = {row["status"]: row["n"] for row in leftover}
                    assert statuses.get("in_progress", 0) == 0, statuses
                    run = pipeline.state.latest_run()
                    assert run["status"] == "failed"
        finally:
            server.shutdown()
            server.server_close()


    def test_reprocess_survives_corrupt_single_record(self):
        """S1.2.2：单条 NULL status_code 的坏记录不拖垮整个 reprocess 任务。"""
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                workspace = root / "work"
                config_path = root / "project.yaml"
                config_path.write_text(yaml.safe_dump({
                    "project": {"name": "reprocess", "workspace": str(workspace)},
                    "source": {"kind": "crawl", "seeds": [
                        f"http://127.0.0.1:{server.server_port}/index",
                        f"http://127.0.0.1:{server.server_port}/page2",
                    ]},
                    "crawl": {"max_pages": 2, "max_depth": 1, "same_host": True, "concurrency": 2},
                    "http": {
                        "user_agent": "ReprocessTest/1.0 (+contact: test@example.org)",
                        "respect_robots": False, "delay_seconds": 0, "allow_private_network": True,
                    },
                    "incremental": {"archive_raw": True},
                    "extract": {"mode": "html", "fields": {"title": {"selector": "title"}}},
                }, sort_keys=False), encoding="utf-8")
                config = load_config(config_path)
                with Pipeline(config) as pipeline:
                    first = pipeline.run()
                    run_id = first["run_id"]
                    # 人为破坏一条响应记录：status_code 写入非数值 → FetchResult 构造时 int() 失败
                    row = pipeline.state.rows(
                        "SELECT id FROM responses WHERE run_id=? AND raw_path IS NOT NULL ORDER BY id LIMIT 1",
                        (run_id,),
                    )[0]
                    with pipeline.state.conn:
                        pipeline.state.conn.execute(
                            "UPDATE responses SET status_code='abc' WHERE id=?", (row["id"],)
                        )
                    summary = pipeline.reprocess_records(run_id)
                    assert summary["failures"] == 1
                    assert summary["reprocessed_responses"] == 1
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
