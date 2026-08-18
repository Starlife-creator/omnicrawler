import tempfile
import unittest
from pathlib import Path

import yaml

from omnicrawler.extraction.api_discovery import (
    discover_api_endpoints,
    redact_payload,
    write_discovery_bundle,
)


class ApiDiscoveryTest(unittest.TestCase):
    def test_infers_items_schema_pagination_and_template(self):
        responses = [{
            "url": "https://api.example.org/v1/items?page=2&limit=50&timestamp=123",
            "method": "GET",
            "status": 200,
            "content_type": "application/json",
            "json": {
                "results": [
                    {"id": 1, "title": "A", "price": 12.5, "updated_at": "2026-07-18T01:02:03Z"},
                    {"id": 2, "title": "B", "price": 9.0, "updated_at": "2026-07-18T01:02:04Z"},
                ],
                "next": "https://api.example.org/v1/items?page=3",
                "total": 120,
            },
        }]
        profiles = discover_api_endpoints(responses)
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].item_path, "results")
        self.assertEqual(profiles[0].schema["properties"]["id"]["type"], "integer")
        self.assertIn("page", profiles[0].pagination)
        self.assertNotIn("timestamp", profiles[0].endpoint)
        with tempfile.TemporaryDirectory() as temp:
            bundle = write_discovery_bundle(responses, Path(temp))
            generated = yaml.safe_load(Path(bundle["templates"][0]).read_text(encoding="utf-8"))
            self.assertEqual(generated["extract"]["item_path"], "results")
            self.assertEqual(generated["source"]["pagination"]["parameter"], "page")
            self.assertIn("title", generated["extract"]["fields"])

    def test_request_payload_redaction_in_generated_templates(self):
        """S1.3.3：生成的模板/报告不含明文登录 token/密码。"""
        responses = [{
            "url": "https://api.example.org/v1/items",
            "method": "POST",
            "status": 200,
            "content_type": "application/json",
            "request_headers": {
                "accept": "application/json",
                "authorization": "Bearer SECRET_TOKEN",
                "x-api-key": "AKIA12345",
            },
            "request_payload": {
                "username": "alice",
                "password": "hunter2",
                "api_key": "AKIA12345",
                "filter": {"category": "books", "token": "c0ffee"},
                "items": ["a", "b"],
            },
            "json": {"results": [{"id": 1, "name": "A"}]},
        }]
        with tempfile.TemporaryDirectory() as temp:
            bundle = write_discovery_bundle(responses, Path(temp))
            report = Path(bundle["report"]).read_text(encoding="utf-8")
            template = Path(bundle["templates"][0]).read_text(encoding="utf-8")
            combined = report + template
            self.assertNotIn("hunter2", combined)
            self.assertNotIn("AKIA12345", combined)
            self.assertNotIn("c0ffee", combined)
            self.assertNotIn("SECRET_TOKEN", combined)
            self.assertIn("***", template)

    def test_redact_payload_recurses_and_keeps_structure(self):
        payload = {
            "login": {"username": "alice", "password": "hunter2"},
            "data": [{"id": 1, "refresh_token": "rt"}],
        }
        redacted = redact_payload(payload)
        self.assertEqual(redacted["login"]["username"], "alice")
        self.assertEqual(redacted["login"]["password"], "***")
        self.assertEqual(redacted["data"][0]["id"], 1)
        self.assertEqual(redacted["data"][0]["refresh_token"], "***")
        self.assertEqual(redact_payload('{"token": "abc", "ok": 1}'), {"token": "***", "ok": 1})
        self.assertEqual(redact_payload("not json"), "not json")
