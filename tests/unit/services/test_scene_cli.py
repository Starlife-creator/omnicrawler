"""批 C 场景管理 CLI（commands/scene.py）单元测试。

覆盖：import（bundled / 用户 YAML）、list、show、candidates 过滤、
accept 验收、maintenance 干跑（不删）/ 执行（--apply 才删）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from omnicrawler.commands import scene as cmd_scene
from omnicrawler.core.config import load_config
from omnicrawler.state.scene_store import SceneDocument, SceneStore

URL = "https://example.com/report"
SCENE = "annual_report"


def _db_workspace(config: str) -> Path:
    """scene 库所在工作区目录（B08-008：场景导入文件须位于其内）。"""
    return load_config(config).workspace


def _config(tmp: Path) -> Path:
    work = tmp / "work"
    config_path = tmp / "project.yaml"
    config_path.write_text(yaml.safe_dump({
        "project": {"name": "sc", "workspace": str(work)},
        "source": {"kind": "static_html", "seeds": [URL]},
    }, sort_keys=False), encoding="utf-8")
    load_config(config_path)  # 验证可加载
    return config_path


def _seed_genes(tmp: Path, config: str) -> None:
    """导入 bundled 后写入一条低适应度基因，供 maintenance 验证。"""
    cmd_scene.execute("import", config=config)
    with SceneStore(tmp / "work" / "scene.sqlite3") as store:
        gene_id = store.upsert_gene(
            SCENE, "company", "span.bad", selector_type="css",
        )
        # hits=0 / misses=4 → fitness=0.0，尝试次数达标且低于阈值
        for _ in range(4):
            store.record_gene_result(gene_id, hit=False)


class SceneCommandTest(unittest.TestCase):
    def test_import_bundled_and_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            config = str(_config(Path(temp)))
            result = cmd_scene.execute("import", config=config)
            self.assertIn("scenes", result)
            listed = cmd_scene.execute("list", config=config)
            scenes = {item["scene"] for item in listed["scenes"]}
            self.assertIn(SCENE, scenes)

    def test_import_user_yaml_and_show(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tmp = Path(temp)
            config = str(_config(tmp))
            # B08-008：场景导入文件须位于工作区内（scene.sqlite3 所在目录）
            scene_yaml = Path(_db_workspace(config)) / "custom.yaml"
            scene_yaml.parent.mkdir(parents=True, exist_ok=True)
            scene_yaml.write_text(yaml.safe_dump({
                "scene": "custom",
                "slots": [
                    {"key": "title", "extractor": "regex",
                     "pattern": "标题[:：]\\s*(.+)", "required": True},
                ],
                "genes": [{"slot": "title", "selector": r"标题[:：]\s*(.+)",
                           "selector_type": "regex"}],
            }, sort_keys=False), encoding="utf-8")
            result = cmd_scene.execute(
                "import", config=config, path=str(scene_yaml),
            )
            self.assertEqual(result["scene"], "custom")
            report = cmd_scene.execute("show", config=config, scene="custom")
            self.assertEqual(report["slots"], ["title"])
            self.assertEqual(report["candidates"], 0)
            self.assertIn("slot_genes", report)

    def test_candidates_and_accept(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tmp = Path(temp)
            config = str(_config(tmp))
            cmd_scene.execute("import", config=config)
            db = tmp / "work" / "scene.sqlite3"
            with SceneStore(db) as store:
                slot_id = store.upsert_slot(store.get_slots(SCENE)[0])
                doc_id = store.get_or_create_document(
                    SceneDocument(document_hash="h1", source_url=URL)
                )
                store.add_candidate(doc_id, slot_id, "示例公司", confidence=0.9)
            pending = cmd_scene.execute(
                "candidates", config=config, scene=SCENE, pending_only=True,
            )["candidates"]
            self.assertEqual(len(pending), 1)
            self.assertFalse(pending[0]["accepted"])
            accepted = cmd_scene.execute(
                "candidates", config=config, scene=SCENE, accepted_only=True,
            )["candidates"]
            self.assertEqual(len(accepted), 0)
            cmd_scene.execute("accept", config=config, candidate_id=pending[0]["id"])
            accepted = cmd_scene.execute(
                "candidates", config=config, scene=SCENE, accepted_only=True,
            )["candidates"]
            self.assertEqual(len(accepted), 1)
            self.assertTrue(accepted[0]["accepted"])

    def test_maintenance_dry_run_does_not_delete(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tmp = Path(temp)
            config = str(_config(tmp))
            _seed_genes(tmp, config)
            result = cmd_scene.execute(
                "maintenance", config=config, scene=SCENE,
                min_fitness=0.2, min_trials=3,
            )
            self.assertTrue(result["dry_run"])
            self.assertEqual(result["will_prune"], 1)
            # 干跑不删：低适应度基因仍在（bundled 4 条未尝试 + 新种 1 条 = 5）
            with SceneStore(tmp / "work" / "scene.sqlite3") as store:
                self.assertEqual(store.gene_stats()["enabled"], 5)

    def test_maintenance_apply_deletes(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            tmp = Path(temp)
            config = str(_config(tmp))
            _seed_genes(tmp, config)
            result = cmd_scene.execute(
                "maintenance", config=config, scene=SCENE,
                min_fitness=0.2, min_trials=3, apply=True,
            )
            self.assertFalse(result.get("dry_run", False))
            self.assertEqual(result["pruned"], 1)
            # 只淘汰新种的低适应度基因；bundled 4 条未尝试不达标，保留
            with SceneStore(tmp / "work" / "scene.sqlite3") as store:
                self.assertEqual(store.gene_stats()["enabled"], 4)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
