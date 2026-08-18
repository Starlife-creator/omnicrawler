"""证据胶囊存储（state/capsule_store.py）单元测试。

覆盖：append/read/count 往返、时间戳与 ID 自动填充、坏行容错、轮转（行数超限/超时）。
"""

from __future__ import annotations

import gzip
import tempfile
import unittest
from pathlib import Path

from omnicrawler.state.capsule_store import Capsule, CapsuleStore


class CapsuleStoreTest(unittest.TestCase):
    def test_run_id_rejects_path_traversal(self) -> None:
        """B04-001：run_id 含路径穿越成分必须被拒绝。"""
        with tempfile.TemporaryDirectory() as temp:
            store = CapsuleStore(Path(temp))
            for evil in ("../../etc/cron.d/x", "a/b", "..\\evil", "a b"):
                with self.assertRaises(ValueError):
                    store.append(evil, Capsule(run_id="r1", action_type="extract_field", action_name="a"))
            # 合法 run_id 不受影响
            store.append("r1", Capsule(run_id="r1", action_type="extract_field", action_name="a"))
            store.append("abc123XYZ_ok", Capsule(run_id="r1", action_type="extract_field", action_name="b"))

    def test_append_read_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = CapsuleStore(Path(temp))
            capsule = Capsule(
                run_id="r1",
                action_type="extract_field",
                action_name="title",
                input={"url": "https://example.com", "rule": {"selector": "h1"}},
                output={"dom_hash": "abc", "value": "标题"},
            )
            path = store.append("r1", capsule)
            self.assertTrue(path.is_file())
            read = store.read("r1")
            self.assertEqual(len(read), 1)
            self.assertEqual(read[0].action_type, "extract_field")
            self.assertEqual(read[0].action_name, "title")
            self.assertEqual(read[0].input["rule"], {"selector": "h1"})
            # 时间戳与 ID 自动填充
            self.assertTrue(read[0].capsule_id)
            self.assertTrue(read[0].timestamp)
            self.assertEqual(store.count("r1"), 1)

    def test_append_keeps_explicit_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = CapsuleStore(Path(temp))
            capsule = Capsule(
                run_id="r1", action_type="extract_field", action_name="x",
                timestamp="2026-08-13T00:00:00.000000",
            )
            store.append("r1", capsule)
            read = store.read("r1")
            self.assertEqual(read[0].timestamp, "2026-08-13T00:00:00.000000")

    def test_read_missing_run_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = CapsuleStore(Path(temp))
            self.assertEqual(store.read("nope"), [])
            self.assertEqual(store.count("nope"), 0)

    def test_bad_line_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = CapsuleStore(Path(temp))
            good = Capsule(run_id="r1", action_type="extract_field", action_name="a")
            store.append("r1", good)
            log = store._run_file("r1")
            with log.open("a", encoding="utf-8") as handle:
                handle.write("{not valid json}\n")
            read = store.read("r1")
            self.assertEqual(len(read), 1)  # 坏行被跳过

    def test_rotate_over_max_lines(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = CapsuleStore(Path(temp), max_lines=2)
            for index in range(4):
                store.append("r1", Capsule(run_id="r1", action_type="extract_field", action_name=str(index)))
            rotated = store.rotate()
            self.assertEqual(rotated, 1)
            self.assertFalse(store._run_file("r1").is_file())
            archive = Path(temp) / "archive" / "r1.log.gz"
            self.assertTrue(archive.is_file())
            with gzip.open(archive, "rt", encoding="utf-8") as handle:
                lines = [line for line in handle if line.strip()]
            self.assertEqual(len(lines), 4)  # 压缩内容完整

    def test_rotate_keeps_young_files(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = CapsuleStore(Path(temp), max_lines=100, keep_days=7)
            store.append("r1", Capsule(run_id="r1", action_type="extract_field", action_name="a"))
            self.assertEqual(store.rotate(), 0)
            self.assertTrue(store._run_file("r1").is_file())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
