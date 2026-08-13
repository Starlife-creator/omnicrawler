"""批 C 场景存储（state/scene_store.py）单元测试。

覆盖：槽位 upsert/查询、文档指纹去重、抽取候选（JSON 往返/验收）、
基因 fitness/推荐/淘汰、场景 YAML 导入（内联 + bundled）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omnicrawl.state.scene_store import (
    SceneDocument,
    SceneStore,
    SlotDefinition,
)

SLOT_YAML = """
scene: demo
name: 演示场景
slots:
  - key: company
    name: 公司名称
    extractor: regex
    pattern: '公司[：:]\\s*([^\\s]+)'
    value_type: text
    required: true
  - key: revenue
    name: 营业收入
    extractor: regex
    pattern: '营业收入\\s*([\\d,.]+)'
    value_type: money
genes:
  - slot: company
    selector: '公司[：:]\\s*([^\\s]+)'
    selector_type: regex
  - slot: revenue
    selector: '营业收入\\s*([\\d,.]+)'
    selector_type: regex
"""


class SceneStoreTest(unittest.TestCase):
    def _store(self, temp: str) -> SceneStore:
        return SceneStore(Path(temp) / "scenes.sqlite3")

    def test_slot_upsert_and_query(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self._store(temp) as store:
                store.upsert_slot(SlotDefinition(
                    scene="s1", slot_key="title", slot_name="标题",
                    extractor_type="regex", pattern=r"标题[:：]\s*(.+)",
                ))
                slots = store.get_slots("s1")
                self.assertEqual(len(slots), 1)
                self.assertEqual(slots[0].slot_key, "title")
                self.assertEqual(slots[0].extractor_type, "regex")
                # 覆盖更新（同 scene+key）
                store.upsert_slot(SlotDefinition(
                    scene="s1", slot_key="title", slot_name="新标题",
                    extractor_type="text", pattern="新版",
                ))
                slots = store.get_slots("s1")
                self.assertEqual(len(slots), 1)
                self.assertEqual(slots[0].slot_name, "新标题")
                self.assertEqual(slots[0].extractor_type, "text")

    def test_list_scenes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self._store(temp) as store:
                store.upsert_slot(SlotDefinition(scene="s1", slot_key="a"))
                store.upsert_slot(SlotDefinition(scene="s1", slot_key="b"))
                store.upsert_slot(SlotDefinition(scene="s2", slot_key="c"))
                scenes = store.list_scenes()
                self.assertEqual({s["scene"] for s in scenes}, {"s1", "s2"})
                by_name = {s["scene"]: s for s in scenes}
                self.assertEqual(by_name["s1"]["slot_count"], 2)

    def test_document_fingerprint_dedup(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self._store(temp) as store:
                doc = SceneDocument(document_hash="abc123", source_url="https://x/1", document_type="html")
                first = store.get_or_create_document(doc)
                second = store.get_or_create_document(doc)
                self.assertEqual(first, second)
                self.assertTrue(store.document_seen("abc123"))
                self.assertFalse(store.document_seen("nope"))

    def test_candidates_roundtrip_and_accept(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self._store(temp) as store:
                slot_id = store.upsert_slot(SlotDefinition(scene="s1", slot_key="price"))
                doc_id = store.get_or_create_document(SceneDocument(document_hash="h1"))
                store.add_candidate(doc_id, slot_id, "12.5", confidence=0.9, evidence={"src": "x"})
                store.add_candidate(doc_id, slot_id, "9.9", confidence=0.5)
                rows = store.candidates(scene="s1")
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]["value"], "9.9")  # 最新在前（id DESC）
                self.assertEqual(rows[0]["confidence"], 0.5)
                self.assertEqual(rows[1]["value"], "12.5")
                self.assertEqual(rows[1]["evidence"], {"src": "x"})
                self.assertFalse(rows[1]["accepted"])
                store.accept_candidate(rows[1]["id"])
                accepted = store.candidates(scene="s1", accepted=True)
                self.assertEqual(len(accepted), 1)
                self.assertEqual(accepted[0]["value"], "12.5")

    def test_gene_fitness_and_top_ranking(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self._store(temp) as store:
                good = store.upsert_gene("s1", "title", "sel-good", selector_type="regex")
                bad = store.upsert_gene("s1", "title", "sel-bad", selector_type="regex")
                store.record_gene_result(good, hit=True)
                store.record_gene_result(good, hit=True)
                store.record_gene_result(good, hit=False)  # 2/3
                store.record_gene_result(bad, hit=False)  # 0/1
                top = store.top_genes("s1", "title", limit=1)
                self.assertEqual(top[0]["selector"], "sel-good")
                self.assertAlmostEqual(top[0]["fitness"], round(2 / 3, 4))
                # min_trials 过滤冷启动
                self.assertEqual(len(store.top_genes("s1", "title", min_trials=2)), 1)
                # 不存在的 slot 无基因
                self.assertEqual(store.top_genes("s1", "other"), [])

    def test_prune_low_fitness_genes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self._store(temp) as store:
                store.upsert_gene("s1", "t", "cold")  # 未尝试，不淘汰
                weak = store.upsert_gene("s1", "t", "weak")
                for _ in range(5):
                    store.record_gene_result(weak, hit=False)  # 0/5
                strong = store.upsert_gene("s1", "t", "strong")
                for _ in range(5):
                    store.record_gene_result(strong, hit=True)  # 5/5
                pruned = store.prune_genes("s1", min_fitness=0.2, min_trials=3)
                self.assertEqual(pruned, 1)  # 只淘汰 weak
                self.assertNotIn("weak", [g["selector"] for g in store.top_genes("s1", "t")])
                stats = store.gene_stats()
                self.assertEqual(stats["enabled"], 2)  # cold + strong

    def test_import_scene_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self._store(temp) as store:
                result = store.import_scene_yaml(SLOT_YAML)
                self.assertEqual(result["scene"], "demo")
                self.assertEqual(result["slots"], 2)
                self.assertEqual(result["genes"], 2)
                slots = store.get_slots("demo")
                self.assertEqual([s.slot_key for s in slots], ["company", "revenue"])
                self.assertEqual(slots[0].extractor_type, "regex")
                # 幂等：重复导入不翻倍
                store.import_scene_yaml(SLOT_YAML)
                self.assertEqual(len(store.get_slots("demo")), 2)

    def test_import_bundled_scenes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with self._store(temp) as store:
                result = store.import_bundled_scenes()
                self.assertGreaterEqual(result["scenes"], 1)
                slots = store.get_slots("annual_report")
                self.assertGreaterEqual(len(slots), 1)
                self.assertTrue(any(s.slot_key == "revenue" for s in slots))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
