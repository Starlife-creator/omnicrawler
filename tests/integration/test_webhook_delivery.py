"""Real local HTTP proves independent receipts, Retry-After and stable event keys."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock

from omnicrawler.core.config import DEFAULTS, AppConfig, deep_merge
from omnicrawler.scheduling.change_detector import ChangeDetector, MonitorRule
from omnicrawler.scheduling.webhook import dispatch_webhooks
from omnicrawler.security.egress import EgressBroker


def test_webhook_recovers_after_desktop_receipt_and_respects_revocation(tmp_path):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            received.append((self.headers.get("Idempotency-Key"), body))
            self.send_response(429 if len(received) == 1 else 204)
            self.send_header("Retry-After", "0")
            self.end_headers()
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/events"
    config = AppConfig(tmp_path / "task.yaml", tmp_path,
                       deep_merge(DEFAULTS, {"source": {"seeds": [url]}, "http": {"allow_private_network": True}}), tmp_path)
    egress = EgressBroker(config)
    rule = MonitorRule(url, rule_id="webhook-rule", check_interval=0, webhook_url=url)
    detector = ChangeDetector(tmp_path, durable_delivery=True)
    detector.add_rule(rule)
    detector._fetch_content = AsyncMock(side_effect=["100", "80", "70"])
    try:
        asyncio.run(detector.check_rule(rule.rule_id))
        event = asyncio.run(detector.check_rule(rule.rule_id))
        detector.acknowledge_notification(event.event_id)
        dispatch_webhooks(detector._store, [rule], egress)
        report = detector.delivery_report()
        assert next(row for row in report if row["target_id"].startswith("webhook"))["status"] == "retrying"
        assert next(row for row in report if row["target_id"] == "desktop")["status"] == "submitted"
        dispatch_webhooks(detector._store, [rule], egress)
        assert len(received) == 2
        assert received[0][0] == received[1][0] == event.event_id
        assert received[1][1]["current_content"] == "80"
        assert received[1][1]["source_kind"] == "page_text"
        assert received[1][1]["envelope_version"] == 1
        asyncio.run(detector.check_rule(rule.rule_id))
        rule.webhook_url = url + "/changed"
        dispatch_webhooks(detector._store, [rule], egress)
        assert len(received) == 2
        assert any(row["status"] == "cancelled" for row in detector.delivery_report())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
