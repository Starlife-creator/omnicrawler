from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

from omnicrawler.core.config import load_config
from omnicrawler.pipeline import Pipeline
from omnicrawler.review.run_compare import compare_runs


def test_business_key_and_ignored_fields_survive_real_title_change(tmp_path):
    value = {"sku": "A", "title": "Old title", "price": "10", "noise": "first"}
    class Api(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"items": [value]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        raw = {"project": {"name": "monitor", "workspace": str(tmp_path / "work")},
               "source": {"kind": "rest", "seeds": [f"http://127.0.0.1:{server.server_port}/items"]},
               "http": {"allow_private_network": True, "respect_robots": False, "delay_seconds": 0},
               "updates": {"enabled": True, "revisit_completed": True, "identity_fields": ["sku"], "ignored_fields": ["noise"]},
               "extract": {"mode": "json", "item_path": "$.items[*]", "fields": {key: {"path": key} for key in value}}}
        path = tmp_path / "task.yaml"
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        with Pipeline(load_config(path)) as pipeline:
            first = pipeline.run()["run_id"]
            value.update(title="New title", price="12", noise="second")
            second = pipeline.run()["run_id"]
            comparison = compare_runs(pipeline.state, first, second)
            assert comparison["modified"] == 1 and comparison["added"] == comparison["removed"] == 0
            assert list(comparison["changes"][0]["modified_fields"]) == ["price", "title"]
            assert comparison["notification_summary"]["changed_fields"] == ["price", "title"]
            value["noise"] = "third"
            third = pipeline.run()["run_id"]
            assert compare_runs(pipeline.state, second, third)["notification_summary"]["total"] == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
