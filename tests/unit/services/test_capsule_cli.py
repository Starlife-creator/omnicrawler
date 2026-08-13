"""B-1 胶囊 CLI（commands/capsule.py）单元测试。

覆盖：timeline 的 run 统计目录 / 单 run 时间线 / limit 截断；
replay 全流程（真实配置 + StateStore + 归档 raw + 胶囊）。
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

import yaml

from omnicrawl.commands import capsule as cmd_capsule
from omnicrawl.core.config import load_config
from omnicrawl.core.models import CrawlRequest, FetchResult
from omnicrawl.state import Capsule, CapsuleStore, StateStore

HTML = "<html><body><h1>标题</h1></body></html>".encode()
URL = "https://example.com/page"


def _append(
    store: CapsuleStore,
    run_id: str,
    *,
    action_name: str = "title",
    rule: dict | None = None,
    value: str = "标题",
    url: str = URL,
) -> None:
    store.append(run_id, Capsule(
        run_id=run_id,
        action_type="extract_field",
        action_name=action_name,
        input={"url": url, "item_selector": "", "rule": rule or {"selector": "h1"}},
        output={
            "dom_hash": hashlib.sha256(HTML).hexdigest(),
            "value": value,
            "trace": {"confidence": 1.0, "selector": "h1"},
        },
    ))


class TimelineCommandTest(unittest.TestCase):
    def test_catalog_lists_runs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = CapsuleStore(Path(temp))
            _append(store, "run-a")
            _append(store, "run-b")
            result = cmd_capsule.timeline("dummy.yaml", capsule_dir=temp)
            self.assertEqual(result["total"], 2)
            self.assertEqual([run["run_id"] for run in result["runs"]], ["run-a", "run-b"])
            self.assertEqual(result["runs"][0]["count"], 1)
            self.assertTrue(result["runs"][0]["first_at"])

    def test_run_timeline_events(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = CapsuleStore(Path(temp))
            _append(store, "r1", action_name="title")
            _append(store, "r1", action_name="price", value="12.5")
            result = cmd_capsule.timeline("dummy.yaml", run_id="r1", capsule_dir=temp)
            self.assertEqual(result["count"], 2)
            self.assertFalse(result["truncated"])
            events = result["events"]
            self.assertEqual(events[0]["action"], "extract_field:title")
            self.assertEqual(events[1]["action"], "extract_field:price")
            self.assertEqual(events[0]["value"], "标题")
            self.assertEqual(events[0]["confidence"], 1.0)
            self.assertEqual(events[1]["rule"], "h1")  # rule dict → selector 摘要
            self.assertEqual(events[0]["url"], URL)

    def test_limit_truncates(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            store = CapsuleStore(Path(temp))
            _append(store, "r1", action_name="a")
            _append(store, "r1", action_name="b")
            result = cmd_capsule.timeline("dummy.yaml", run_id="r1", capsule_dir=temp, limit=1)
            self.assertTrue(result["truncated"])
            self.assertEqual(len(result["events"]), 1)


class ReplayCommandTest(unittest.TestCase):
    def test_replay_ok_full_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            work = temp / "work"
            config_path = temp / "project.yaml"
            config_path.write_text(yaml.safe_dump({
                "project": {"name": "rp", "workspace": str(work)},
                "source": {"kind": "static_html", "seeds": [URL]},
            }, sort_keys=False), encoding="utf-8")
            load_config(config_path)  # 验证配置可加载（workspace 自动建）

            state = StateStore(work / "state.sqlite3")
            run_id = state.start_run("rp", str(config_path))
            raw = work / "page.html"
            raw.write_bytes(HTML)
            request = CrawlRequest(URL)
            result = FetchResult(request, URL, 200, {"content-type": "text/html"}, HTML, 0.1)
            state.save_response(run_id, result, str(raw))
            state.close()

            _append(CapsuleStore(work / "capsules"), run_id)

            outcome = cmd_capsule.replay(str(config_path), run_id=run_id, field="title")
            self.assertEqual(outcome["status"], "ok")
            self.assertEqual(outcome["value"], "标题")
            self.assertEqual(outcome["url"], URL)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
