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


if __name__ == "__main__":
    unittest.main()
