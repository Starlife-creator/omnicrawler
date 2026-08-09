import tempfile
import unittest
from pathlib import Path

from omnicrawl.core.config import AppConfig
from omnicrawl.core.models import ExtractedRecord
from omnicrawl.quality.data_intelligence import enrich_records, hamming_distance, normalize_entity, simhash
from omnicrawl.quality.semantic_changes import compare_record_data, record_identity, semantic_hash


class SemanticAndIntelligenceTest(unittest.TestCase):
    def test_semantic_change_ignores_timestamp_but_reports_business_field(self):
        before = {"id": 1, "title": "公告 A", "amount": 100, "updated_at": "old"}
        after = {"id": 1, "title": "公告 A", "amount": 120, "updated_at": "new"}
        change = compare_record_data(before, after, identity=record_identity(after))
        self.assertEqual(change.change_type, "modified")
        self.assertEqual(change.modified_fields, ("amount",))
        self.assertEqual(semantic_hash(before), semantic_hash({**before, "updated_at": "new"}))

    def test_entity_resolution_and_near_duplicate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = AppConfig(root / "c.yaml", root, {
                "project": {"name": "x", "workspace": str(root / "work")},
                "data_quality": {
                    "entity_fields": ["company"],
                    "entity_resolution": {"aliases": {"北京示例科技有限公司": ["示例科技", "北京示例科技"]}},
                    "near_duplicate_fields": ["title"],
                    "near_duplicate_hamming": 8,
                },
            }, root / "work")
            records = [
                ExtractedRecord("u1", "item", {"company": "示例科技", "title": "关于项目 A 的研究公告"}),
                ExtractedRecord("u2", "item", {"company": "北京示例科技", "title": "关于项目 A 的研究公告"}),
            ]
            summary = enrich_records(records, config)
            self.assertEqual(records[0].data["company"], "北京示例科技有限公司")
            self.assertEqual(summary["entities_resolved"], 2)
            self.assertEqual(summary["near_duplicates"], 1)
            self.assertEqual(hamming_distance(simhash("same text"), simhash("same text")), 0)
            self.assertEqual(normalize_entity("示例科技有限公司"), "示例科技")
