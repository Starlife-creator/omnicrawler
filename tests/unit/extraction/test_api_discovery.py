import tempfile
import unittest
from pathlib import Path

import yaml

from omnicrawl.extraction.api_discovery import discover_api_endpoints, write_discovery_bundle


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
