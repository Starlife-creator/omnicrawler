"""B-1 证据胶囊埋点（pipeline/_extract.py）端到端测试。

覆盖：默认关闭（无 OMNICRAWL_CAPSULE_ENABLED 不写日志）；开启后每个字段
写一条胶囊，记录输入规则 / URL / dom_hash / 提取值。
"""

from __future__ import annotations

import hashlib
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.pipeline import Pipeline
from omnicrawler.state.capsule_store import CapsuleStore

HTML = "<html><body><h1>标题</h1></body></html>"


@pytest.fixture
def http_server():
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML.encode("utf-8"))

        def log_message(self, *args) -> None:  # noqa: N802
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()


def _write_config(tmp_path, url: str) -> object:
    config_path = tmp_path / "project.yaml"
    config_path.write_text(yaml.safe_dump({
        "project": {"name": "capsule", "workspace": str(tmp_path / "work")},
        "source": {"kind": "static_html", "seeds": [url]},
        "http": {"respect_robots": False, "allow_private_network": True, "delay_seconds": 0},
        "extract": {"mode": "html", "fields": {"title": {"selector": "h1"}}},
    }, sort_keys=False), encoding="utf-8")
    return load_config(config_path)


def test_capsules_gated_off_by_default(tmp_path, http_server, monkeypatch) -> None:
    monkeypatch.delenv("OMNICRAWL_CAPSULE_ENABLED", raising=False)
    config = _write_config(tmp_path, http_server)
    with Pipeline(config) as pipeline:
        pipeline.run()
    capsules_dir = config.workspace / "capsules"
    assert not capsules_dir.exists() or not list(capsules_dir.glob("*.log"))


def test_capsules_written_when_enabled(tmp_path, http_server, monkeypatch) -> None:
    monkeypatch.setenv("OMNICRAWL_CAPSULE_ENABLED", "true")
    config = _write_config(tmp_path, http_server)
    with Pipeline(config) as pipeline:
        summary = pipeline.run()
    capsules = CapsuleStore(config.workspace / "capsules").read(summary["run_id"])
    assert len(capsules) == 1
    capsule = capsules[0]
    assert capsule.action_type == "extract_field"
    assert capsule.action_name == "title"
    assert str(capsule.input["url"]).startswith("http://127.0.0.1:")
    assert capsule.input["rule"] == {"selector": "h1"}
    assert capsule.output["value"] == "标题"
    assert capsule.output["dom_hash"] == hashlib.sha256(HTML.encode("utf-8")).hexdigest()


def test_capsules_replay_each_record_and_pin_historical_response(tmp_path, http_server, monkeypatch):
    import sys

    from omnicrawler.core.models import CrawlRequest, FetchResult
    from omnicrawler.services.replay import replay_field
    from omnicrawler.state import StateStore

    monkeypatch.setenv("OMNICRAWL_CAPSULE_ENABLED", "true")
    monkeypatch.setattr(sys.modules[__name__], "HTML", '<article><h2>A</h2></article><article><h2>B</h2></article>')
    config = _write_config(tmp_path, http_server)
    config.raw["extract"].update(item_selector="article", fields={"title": {"selector": "h2"}})
    with Pipeline(config) as pipeline:
        summary = pipeline.run()
    run_id = summary["run_id"]
    capsules = CapsuleStore(config.workspace / "capsules").read(run_id)
    assert [capsule.output["value"] for capsule in capsules] == ["A", "B"]
    assert [capsule.input["record_index"] for capsule in capsules] == [1, 2]
    assert capsules[0].input["record_id"] != capsules[1].input["record_id"]
    with StateStore(config.workspace / "state.sqlite3") as state:
        unrelated = config.workspace / "newer.html"
        unrelated.write_bytes(b"<h2>Newer unrelated response</h2>")
        request = CrawlRequest(http_server)
        state.save_response(run_id, FetchResult(request, http_server, 200, {}, unrelated.read_bytes(), 0), str(unrelated))
        assert replay_field(run_id, "title", store=state)["status"] == "ambiguous_record"
        result = replay_field(run_id, "title", record_index=2, response_id=capsules[1].input["response_id"], store=state)
        assert result["status"] == "ok" and result["value"] == "B"
        assert replay_field(run_id, "title", capsule_id=capsules[0].capsule_id, store=state)["value"] == "A"


def test_capsule_capture_reports_capacity_omissions(tmp_path):
    from omnicrawler.state.capsule_store import Capsule

    store = CapsuleStore(tmp_path, max_lines=1)
    capture = store.append_many("run", [Capsule("run", "extract_field"), Capsule("run", "extract_field")])
    assert capture == {"written": 1, "omitted": 1}
    assert store.append_many("run", [Capsule("run", "extract_field")]) == {"written": 0, "omitted": 1}
    assert store.count("run") == 1
