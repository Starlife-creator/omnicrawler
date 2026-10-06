"""Real crawl, atomic notices and selected HTTP retry share one task identity."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.pipeline import Pipeline
from omnicrawler.pipeline._run import _comparison_scope
from omnicrawler.services.record_notifications import dispatch, report
from omnicrawler.state import StateStore


def test_actual_record_workflow_recovers_notification_without_recollecting(tmp_path):
    received = []
    value = [100]
    hits = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            body = (b"User-agent: *\nAllow: /\n" if self.path == "/robots.txt" else
                    json.dumps({"items": [{"id": 1, "price": value[0], "in_stock": False}]}).encode())
            self.send_response(200)
            self.send_header("Content-Type", "text/plain" if self.path == "/robots.txt" else "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            received.append((self.headers.get("Idempotency-Key"), self.headers.get("Cookie"),
                             json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(429 if len(received) == 1 else 204)
            self.send_header("Retry-After", "0")
            self.end_headers()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    raw = {
        "project": {"name": "Record notices", "task_id": "record-notice-fixture", "workspace": str(tmp_path / "work")},
        "source": {"kind": "rest", "seeds": [url + "/items"]},
        "http": {"allow_private_network": True, "respect_robots": True, "delay_seconds": 0, "retries": 0},
        "crawl": {"max_pages": 1, "allow_domains": ["127.0.0.1"], "concurrency": 1},
        "extract": {"mode": "json", "item_path": "$.items[*]",
                    "fields": {"id": {"path": "id"}, "price": {"path": "price"}, "in_stock": {"path": "in_stock"}}},
        "updates": {"identity_fields": ["id"], "notifications": {"enabled": True, "webhook_url": url + "/events"}},
        "outputs": {"jsonl": True, "csv": True, "xlsx": True},
    }
    path = tmp_path / "task.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf8")
    config = load_config(path)
    try:
        with Pipeline(config) as pipeline:
            baseline = pipeline.run()
        assert baseline["status"] == "succeeded" and received == []
        value[0] = 0
        with Pipeline(config) as pipeline:
            changed = pipeline.run()
        assert changed["status"] == "succeeded"
        assert changed["notifications"]["deliveries"][0]["status"] == "retrying"
        assert len(received) == 1 and received[0][1] is None
        event = received[0][0]
        assert received[0][2]["details"]["after"] == {"id": 1, "price": 0, "in_stock": False}
        prior_hits = list(hits)
        restored = dispatch(config, force=True, event_ids={event})
        assert hits == prior_hits and len(received) == 2
        assert received[1][0] == event
        assert restored["deliveries"][0]["status"] == "submitted"
        with Pipeline(config) as pipeline:
            unchanged = pipeline.run()
        assert unchanged["status"] == "succeeded" and len(received) == 2
        with pytest.raises(ValueError, match="当前任务"):
            dispatch(config, force=True, event_ids={"foreign-event"})
        raw["updates"]["notifications"]["webhook_url"] += "/other"
        path.write_text(yaml.safe_dump(raw), encoding="utf8")
        assert _comparison_scope(config) == _comparison_scope(load_config(path))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_disabling_one_task_only_revokes_its_own_deliveries(tmp_path):
    from omnicrawler.core.config import DEFAULTS, AppConfig, deep_merge
    from omnicrawler.core.models import ExtractedRecord
    from omnicrawler.services.record_notifications import notification_binding

    workspace = tmp_path / "work"
    configs = [AppConfig(tmp_path / f"{task}.yaml", tmp_path, deep_merge(DEFAULTS, {
        "project": {"name": task, "task_id": task},
        "updates": {"notifications": {"enabled": True, "webhook_url": "https://example.test/events"}},
    }), workspace) for task in ("A", "B")]
    with StateStore(workspace / "state.sqlite3") as store:
        for config in configs:
            for price in (100, 80):
                run = store.start_run(config.project_name, str(config.path), task_id=config.project_name)
                store.track_semantic_changes(run, [ExtractedRecord("https://example.test/item", "item", {"id": 1, "price": price})],
                                             identity_fields=("id",), notification=notification_binding(config))
    configs[0].raw["updates"]["notifications"]["enabled"] = False
    assert dispatch(configs[0])["deliveries"][0]["status"] == "cancelled"
    assert report(configs[1])["deliveries"][0]["status"] == "pending"
