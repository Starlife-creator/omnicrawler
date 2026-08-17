"""批 C-2 基因池（quality/gene_pool.py + services/gene_maintenance.py）测试。

覆盖：fitness 计算、GenePool 播种/反馈/推荐、维护（bundled 导入、
低适应度淘汰、场景报告）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from omnicrawler.quality.gene_pool import Gene, GenePool, fitness
from omnicrawler.services.gene_maintenance import import_scenes, run_maintenance, scene_report
from omnicrawler.state.scene_store import SceneStore


class GenePoolTest(unittest.TestCase):
    def _pool(self, temp: str) -> tuple[SceneStore, GenePool]:
        store = SceneStore(Path(temp) / "genes.sqlite3")
        return store, GenePool(store)

    def test_fitness_function(self) -> None:
        assert fitness(2, 2) == 0.5
        assert fitness(0, 0) == 0.0
        assert fitness(5, 0) == 1.0

    def test_seed_and_record_update_fitness(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, pool = self._pool(temp)
            try:
                pool.seed("s1", "title", "sel-a", selector_type="regex")
                pool.record("s1", "title", "sel-a", hit=True)
                pool.record("s1", "title", "sel-a", hit=False)
                genes = pool.recommend("s1", "title")
                self.assertEqual(len(genes), 1)
                self.assertEqual(genes[0].hits, 1)
                self.assertEqual(genes[0].misses, 1)
                self.assertAlmostEqual(genes[0].fitness, 0.5)
            finally:
                store.close()

    def test_recommend_ranks_by_fitness(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store, pool = self._pool(temp)
            try:
                pool.record("s1", "title", "good", hit=True)
                pool.record("s1", "title", "good", hit=True)
                pool.record("s1", "title", "good", hit=True)
                pool.record("s1", "title", "poor", hit=False)
                top = pool.recommend("s1", "title", limit=1)
                self.assertEqual(top[0].selector, "good")
                # min_trials 过滤冷启动（poor 只有 1 次尝试）
                self.assertEqual(len(pool.recommend("s1", "title", min_trials=2)), 1)
            finally:
                store.close()

    def test_gene_dataclass_fields(self) -> None:
        gene = Gene(scene="s", slot_key="k", selector="sel")
        self.assertEqual(gene.fitness, 0.0)
        self.assertTrue(gene.enabled)


class GeneMaintenanceTest(unittest.TestCase):
    def test_import_bundled_scenes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with SceneStore(Path(temp) / "g.sqlite3") as store:
                result = import_scenes(store)
                self.assertGreaterEqual(result["scenes"], 1)
                slots = store.get_slots("annual_report")
                self.assertGreaterEqual(len(slots), 4)  # company/revenue/net_profit/report_date

    def test_run_maintenance_prunes_weak_genes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with SceneStore(Path(temp) / "g.sqlite3") as store:
                pool = GenePool(store)
                pool.record("s1", "t", "weak", hit=False)
                pool.record("s1", "t", "weak", hit=False)
                pool.record("s1", "t", "weak", hit=False)
                pool.record("s1", "t", "strong", hit=True)
                pool.record("s1", "t", "strong", hit=True)
                result = run_maintenance(store, scene="s1", min_fitness=0.2, min_trials=3)
                self.assertEqual(result["pruned"], 1)
                self.assertEqual(result["enabled"], 1)

    def test_scene_report_summary(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            with SceneStore(Path(temp) / "g.sqlite3") as store:
                import_scenes(store)
                pool = GenePool(store)
                pool.record("annual_report", "company", "公司[：:]\\s*([^\\s]+)", hit=True)
                report = scene_report(store, "annual_report")
                self.assertIn("company", report["slot_genes"])
                genes = report["slot_genes"]["company"]
                self.assertTrue(genes)
                self.assertGreaterEqual(genes[0]["fitness"], 0.5)
                self.assertGreaterEqual(len(report["slots"]), 4)

    # ── N5：maybe_maintain 惰性维护 ──────────────────────
    @staticmethod
    def _reset_maintain_clock() -> None:
        import omnicrawler.services.gene_maintenance as gm

        # 用极大负值代替 0.0：time.monotonic() 是系统启动以来的时钟，
        # 全新 CI runner（开机 < ttl=300s）上 0.0 会令首调被节流而误挂。
        gm._LAST_CHECK = -1e9

    def test_maybe_maintain_no_prune_below_threshold(self) -> None:
        self._reset_maintain_clock()
        with tempfile.TemporaryDirectory() as temp:
            with SceneStore(Path(temp) / "g.sqlite3") as store:
                pool = GenePool(store)
                pool.record("s1", "t", "only", hit=True)
                from omnicrawler.services.gene_maintenance import maybe_maintain

                # total=1 < max_genes=100 → 不维护
                self.assertFalse(maybe_maintain(store, max_genes=100))
                self.assertEqual(store.gene_stats()["total"], 1)

    def test_maybe_maintain_prunes_above_threshold(self) -> None:
        self._reset_maintain_clock()
        with tempfile.TemporaryDirectory() as temp:
            with SceneStore(Path(temp) / "g.sqlite3") as store:
                pool = GenePool(store)
                # 播种 6 个弱基因（各 3 次 miss → fitness=0，尝试达标）
                for i in range(6):
                    pool.record("s1", "t", f"weak{i}", hit=False)
                    pool.record("s1", "t", f"weak{i}", hit=False)
                    pool.record("s1", "t", f"weak{i}", hit=False)
                from omnicrawler.services.gene_maintenance import maybe_maintain

                self.assertTrue(maybe_maintain(store, max_genes=5))
                self.assertEqual(store.gene_stats()["total"], 0)  # 全部弱基因被淘汰

    def test_maybe_maintain_ttl_throttle(self) -> None:
        self._reset_maintain_clock()
        with tempfile.TemporaryDirectory() as temp:
            with SceneStore(Path(temp) / "g.sqlite3") as store:
                pool = GenePool(store)
                for i in range(6):
                    pool.record("s1", "t", f"weak{i}", hit=False)
                from omnicrawler.services.gene_maintenance import maybe_maintain

                # 第一次触发维护
                self.assertTrue(maybe_maintain(store, max_genes=5, ttl_seconds=300))
                # TTL 内第二次调用 → 直接节流跳过（即使又膨胀）
                pool.record("s1", "t", "again", hit=False)
                self.assertFalse(maybe_maintain(store, max_genes=5, ttl_seconds=300))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
