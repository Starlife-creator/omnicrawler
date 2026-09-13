import tempfile
import unittest
from pathlib import Path

from omnicrawler.core.config import AppConfig
from omnicrawler.core.models import ExtractedRecord
from omnicrawler.quality.data_intelligence import enrich_records, hamming_distance, normalize_entity, simhash
from omnicrawler.quality.semantic_changes import compare_record_data, record_identity, semantic_hash


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


def test_record_identity_recognizes_chinese_field_names() -> None:
    """中文键必须参与身份判定，否则改价会被误判成「删除+新增」。

    本项目分析器产出的字段名是中文（见 intelligent_scraper 的 _FIELD_RULES 与
    _ITEMPROP_NAMES：标题 / 名称 / 编号 / 链接地址）。``record_identity`` 原先只认
    英文键 ⇒ 中文配置下退化成"按内容哈希取身份" ⇒ 任何字段变化都会换身份 ⇒
    变更语义从「修改」静默失真为「删除+新增」。
    """
    from omnicrawler.quality.semantic_changes import record_identity

    url = "http://example.org/list"
    assert record_identity({"标题": "甲", "价格": "1"}, url) == record_identity(
        {"标题": "甲", "价格": "10"}, url
    ), "改价不应换身份"
    assert record_identity({"编号": "A1", "价格": "1"}, url) == record_identity(
        {"编号": "A1", "价格": "10"}, url
    ), "编号应作为身份"
    assert record_identity({"链接地址": "/p1", "价格": "1"}, url) == record_identity(
        {"链接地址": "/p1", "价格": "10"}, url
    ), "链接地址应作为身份"
    # 英文键行为不变（不因本次修复而改变既有取值顺序）
    assert record_identity({"title": "甲"}, url) == "title:甲"
    # 真不同的记录仍要能区分
    assert record_identity({"标题": "甲"}, url) != record_identity({"标题": "乙"}, url)
