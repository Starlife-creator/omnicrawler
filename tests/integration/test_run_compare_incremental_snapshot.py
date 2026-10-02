"""Real HTTP incremental cycles must compare observations rather than new delivery."""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from omnicrawler.core.config import load_config
from omnicrawler.pipeline import Pipeline
from omnicrawler.review.run_compare import compare_runs
from omnicrawler.state import StateStore


def test_http_incremental_cycles_preserve_unchanged_and_empty_snapshots(tmp_path: Path) -> None:
    data = [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}]

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = json.dumps(data).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(json.dumps({
        "project": {"name": "compare", "workspace": str(tmp_path / "work")},
        "source": {"kind": "rest", "seeds": [f"http://127.0.0.1:{server.server_port}/items"]},
        "extract": {"mode": "json", "item_path": "$[*]", "fields": {
            "id": {"path": "id"}, "title": {"path": "title"},
        }},
        "http": {"allow_private_network": True, "respect_robots": False, "delay_seconds": 0},
        "updates": {"enabled": True, "revisit_completed": True},
        "plugins": {"paths": []},
    }), encoding="utf-8")
    config = load_config(config_path)
    try:
        runs = []
        for _ in range(2):
            with Pipeline(config) as pipeline:
                runs.append(pipeline.run())
        assert runs[0]["records"] == 2
        assert runs[1]["records"] == 0  # Incremental delivery remains unchanged.
        with StateStore(config.workspace / "state.sqlite3") as state:
            assert compare_runs(state, runs[0]["run_id"], runs[1]["run_id"])["changes"] == []
        data.clear()
        with Pipeline(config) as pipeline:
            empty = pipeline.run()
        with StateStore(config.workspace / "state.sqlite3") as state:
            report = compare_runs(state, runs[1]["run_id"], empty["run_id"])
            assert report["removed"] == 2
            assert report["possibly_removed"] == 0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
