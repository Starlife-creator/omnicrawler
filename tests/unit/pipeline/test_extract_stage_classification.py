"""S2.5.33：提取异常阶段归类（stage="extract"，非 "fetch"）。"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import yaml

from omnicrawl.core.config import load_config
from omnicrawl.pipeline import Pipeline


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"<html><title>Extract Boom</title><p>data</p></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):  # noqa: N802
        return


def test_extraction_error_recorded_with_extract_stage(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        config_path = tmp_path / "project.yaml"
        config_path.write_text(yaml.safe_dump({
            "project": {"name": "boom", "workspace": str(tmp_path / "work")},
            "source": {"kind": "static_html", "seeds": [f"http://127.0.0.1:{server.server_port}/"]},
            "http": {"respect_robots": False, "allow_private_network": True, "delay_seconds": 0},
            "extract": {"mode": "html", "fields": {"title": {"selector": "title"}}},
        }, sort_keys=False), encoding="utf-8")
        config = load_config(config_path)
        with Pipeline(config) as pipeline:
            original = pipeline.registry.processors["html"]

            class _BoomProcessor(original):
                def process(self, result):
                    raise ValueError("broken rule")

            pipeline.registry.processors["html"] = _BoomProcessor
            summary = pipeline.run()
            rows = pipeline.state.rows(
                "SELECT stage, error_type, message FROM errors WHERE run_id=?",
                (summary["run_id"],),
            )
            assert rows and rows[0]["stage"] == "extract"
            assert rows[0]["error_type"] == "ExtractionError"
            assert "broken rule" in rows[0]["message"]
    finally:
        server.shutdown()
        server.server_close()
