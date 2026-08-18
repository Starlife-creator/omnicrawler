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
