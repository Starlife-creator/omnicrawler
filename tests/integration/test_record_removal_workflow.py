"""Actual HTTP completion confirms removals; a return restores entity presence."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.pipeline import Pipeline


@pytest.mark.parametrize("include_removed", [True, False])
def test_complete_removal_and_reappearance_share_durable_notices(tmp_path, include_removed):
    items, received = [{"id": 1, "price": 100}], []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps({"items": items}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    raw = {
        "project": {"name": "Removals", "task_id": "removal-fixture", "workspace": str(tmp_path / "work")},
        "source": {"kind": "rest", "seeds": [base + "/items"]},
        "http": {"allow_private_network": True, "respect_robots": False, "delay_seconds": 0, "retries": 0},
        "crawl": {"max_pages": 4, "allow_domains": ["127.0.0.1"], "concurrency": 1},
        "extract": {"mode": "json", "item_path": "$.items[*]", "fields": {"id": {"path": "id"}, "price": {"path": "price"}}},
        "updates": {"enabled": True, "use_conditional_requests": False, "identity_fields": ["id"],
                    "notifications": {"enabled": True, "include_removed": include_removed, "webhook_url": base + "/events"}},
        "outputs": {"jsonl": True, "csv": False, "xlsx": False},
    }
    path = tmp_path / "task.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    config = load_config(path)
    try:
        with Pipeline(config) as pipeline:
            assert pipeline.run()["status"] == "succeeded"
        assert received == []
        items.clear()
        with Pipeline(config) as pipeline:
            removed = pipeline.run()
        assert removed["status"] == "succeeded"
        assert len(received) == int(include_removed)
        if include_removed:
            assert received[0]["details"]["change_type"] == "removed"
            assert received[0]["details"]["confirmed"] is True
            assert received[0]["details"]["before"] == {"id": 1, "price": 100}
            assert received[0]["details"]["after"] is None
        with Pipeline(config) as pipeline:
            assert pipeline.run()["status"] == "succeeded"
        assert len(received) == int(include_removed)
        items.append({"id": 1, "price": 100})
        with Pipeline(config) as pipeline:
            assert pipeline.run()["status"] == "succeeded"
        if include_removed:
            assert len(received) == 2 and received[1]["details"]["change_type"] == "added"
            assert received[1]["details"]["before"] is None
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
