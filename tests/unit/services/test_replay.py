"""限定重放（services/replay.py）单元测试。

覆盖：ok 全流程、no_capsule（无胶囊/字段不匹配/阶段过滤）、archive_missing、
dom_changed 完整性校验、timeout（子进程超时）、error（子进程提取失败/胶囊缺规则）。
"""

from __future__ import annotations

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from omnicrawl.core.models import CrawlRequest, FetchResult
from omnicrawl.services.replay import replay_field
from omnicrawl.state import Capsule, CapsuleStore, StateStore

HTML = b"<html><body><h1>\xe6\xa0\x87\xe9\xa2\x98</h1></body></html>"  # <h1>标题</h1>
URL = "https://example.com/page"


class ReplayTest(unittest.TestCase):
    def _seed(self, state: StateStore, run_id: str, *, raw_path: str | None, html: bytes = HTML) -> None:
        request = CrawlRequest(URL)
        result = FetchResult(request, URL, 200, {"content-type": "text/html"}, html, 0.1)
        state.save_response(run_id, result, raw_path)

    def _capsule(
        self,
        run_id: str,
        *,
        action_name: str = "title",
        input_data: dict | None = None,
        output: dict | None = None,
    ) -> Capsule:
        return Capsule(
            run_id=run_id,
            action_type="extract_field",
            action_name=action_name,
            input=input_data or {
                "url": URL,
                "rule": {"selector": "h1"},
            },
            output=output or {"dom_hash": hashlib.sha256(HTML).hexdigest()},
        )

    def test_ok_full_flow(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            with StateStore(temp / "state.sqlite3") as state:
                run_id = state.start_run("replay_test", "config.yaml")
                raw = temp / "raw" / "page.html"
                raw.parent.mkdir(parents=True, exist_ok=True)
                raw.write_bytes(HTML)
                self._seed(state, run_id, raw_path=str(raw))
                capsule_dir = temp / "capsules"
                CapsuleStore(capsule_dir).append(run_id, self._capsule(run_id))
                result = replay_field(run_id, "title", store=state, capsule_dir=capsule_dir)
                self.assertEqual(result["status"], "ok")
                self.assertEqual(result["value"], "标题")
                self.assertEqual(result["url"], URL)
                self.assertIsNotNone(result["dom_hash"])

    def test_no_capsule(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            with StateStore(temp / "state.sqlite3") as state:
                run_id = state.start_run("replay_test", "config.yaml")
                result = replay_field(run_id, "title", store=state, capsule_dir=temp / "capsules")
                self.assertEqual(result["status"], "no_capsule")

    def test_no_capsule_when_field_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            with StateStore(temp / "state.sqlite3") as state:
                run_id = state.start_run("replay_test", "config.yaml")
                CapsuleStore(temp / "capsules").append(run_id, self._capsule(run_id, action_name="price"))
                result = replay_field(run_id, "title", store=state, capsule_dir=temp / "capsules")
                self.assertEqual(result["status"], "no_capsule")

    def test_no_capsule_when_stage_filtered(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            with StateStore(temp / "state.sqlite3") as state:
                run_id = state.start_run("replay_test", "config.yaml")
                CapsuleStore(temp / "capsules").append(run_id, self._capsule(run_id))
                result = replay_field(run_id, "title", stage="normalize", store=state, capsule_dir=temp / "capsules")
                self.assertEqual(result["status"], "no_capsule")

    def test_archive_missing_when_file_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            with StateStore(temp / "state.sqlite3") as state:
                run_id = state.start_run("replay_test", "config.yaml")
                self._seed(state, run_id, raw_path=str(temp / "gone.html"))
                CapsuleStore(temp / "capsules").append(run_id, self._capsule(run_id))
                result = replay_field(run_id, "title", store=state, capsule_dir=temp / "capsules")
                self.assertEqual(result["status"], "archive_missing")

    def test_archive_missing_when_no_raw_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            with StateStore(temp / "state.sqlite3") as state:
                run_id = state.start_run("replay_test", "config.yaml")
                self._seed(state, run_id, raw_path=None)
                CapsuleStore(temp / "capsules").append(run_id, self._capsule(run_id))
                result = replay_field(run_id, "title", store=state, capsule_dir=temp / "capsules")
                self.assertEqual(result["status"], "archive_missing")

    def test_dom_changed_on_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            with StateStore(temp / "state.sqlite3") as state:
                run_id = state.start_run("replay_test", "config.yaml")
                raw = temp / "raw" / "page.html"
                raw.parent.mkdir(parents=True, exist_ok=True)
                raw.write_bytes(b"<html><body>changed</body></html>")
                self._seed(state, run_id, raw_path=str(raw), html=b"<html><body>changed</body></html>")
                CapsuleStore(temp / "capsules").append(
                    run_id, self._capsule(run_id, output={"dom_hash": "deadbeef"})
                )
                result = replay_field(run_id, "title", store=state, capsule_dir=temp / "capsules")
                self.assertEqual(result["status"], "dom_changed")
                self.assertEqual(result["message"], "archive_hash_mismatch")

    def test_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            with StateStore(temp / "state.sqlite3") as state:
                run_id = state.start_run("replay_test", "config.yaml")
                raw = temp / "raw" / "page.html"
                raw.parent.mkdir(parents=True, exist_ok=True)
                raw.write_bytes(HTML)
                self._seed(state, run_id, raw_path=str(raw))
                CapsuleStore(temp / "capsules").append(run_id, self._capsule(run_id))
                with patch(
                    "omnicrawl.services.replay.subprocess.run",
                    side_effect=subprocess.TimeoutExpired(cmd="replay", timeout=0.01),
                ):
                    result = replay_field(run_id, "title", store=state, capsule_dir=temp / "capsules")
                self.assertEqual(result["status"], "timeout")

    def test_error_on_item_selector_no_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            with StateStore(temp / "state.sqlite3") as state:
                run_id = state.start_run("replay_test", "config.yaml")
                raw = temp / "raw" / "page.html"
                raw.parent.mkdir(parents=True, exist_ok=True)
                raw.write_bytes(HTML)
                self._seed(state, run_id, raw_path=str(raw))
                capsule = self._capsule(
                    run_id,
                    input_data={
                        "url": URL,
                        "item_selector": ".list .item",
                        "rule": {"selector": "h1"},
                    },
                )
                CapsuleStore(temp / "capsules").append(run_id, capsule)
                result = replay_field(run_id, "title", store=state, capsule_dir=temp / "capsules")
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["message"], "item_selector_no_match")

    def test_error_when_rule_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            with StateStore(temp / "state.sqlite3") as state:
                run_id = state.start_run("replay_test", "config.yaml")
                capsule = self._capsule(run_id, input_data={"url": URL})
                CapsuleStore(temp / "capsules").append(run_id, capsule)
                result = replay_field(run_id, "title", store=state, capsule_dir=temp / "capsules")
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["message"], "capsule_rule_missing")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
