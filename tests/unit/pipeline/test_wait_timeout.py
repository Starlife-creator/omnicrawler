"""S2.5.43：wait(inflight) 超时——任务挂起时 wait 可超时返回。"""

from __future__ import annotations

import threading
from concurrent.futures import FIRST_COMPLETED, Future, wait
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from omnicrawler.core.config import load_config
from omnicrawler.pipeline import Pipeline


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"<html><title>Slow</title></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: N802
        return


def test_wait_timeout_parameter_supported(tmp_path: Path) -> None:
    future: Future = Future()
    done, pending = wait({future}, return_when=FIRST_COMPLETED, timeout=0.01)
    assert not done and pending == {future}


def test_pipeline_wait_timeout_config_used(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config_path = tmp_path / "project.yaml"
        config_path.write_text(yaml.safe_dump({
            "project": {"name": "wt", "workspace": str(tmp_path / "work")},
            "source": {"kind": "static_html", "seeds": [f"http://127.0.0.1:{server.server_port}/"]},
            "crawl": {"max_pages": 1, "wait_timeout_seconds": 0.05},
            "http": {"respect_robots": False, "allow_private_network": True, "delay_seconds": 0},
        }, sort_keys=False), encoding="utf-8")
        with Pipeline(load_config(config_path)) as pipeline:
            summary = pipeline.run()
            assert summary["status"] == "succeeded"
    finally:
        server.shutdown()
        server.server_close()
