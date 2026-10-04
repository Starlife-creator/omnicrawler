from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

from omnicrawler.core.config import load_config
from omnicrawler.pipeline import Pipeline
from omnicrawler.templates.capture import capture, trial_reference
from omnicrawler.templates.template_catalog import bundled_template_catalog


def test_captured_cursor_task_delivers_all_pages_via_real_http(tmp_path):
    class Api(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/robots.txt":
                body, kind = b"User-agent: *\nAllow: /\n", "text/plain"
            else:
                last = "cursor=last" in self.path
                body = json.dumps({"items": [{"id": 2 if last else 1}], "next": None if last else "last"}).encode()
                kind = "application/json"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        path = tmp_path / "original.yaml"
        raw = {"project": {"name": "capture-api", "workspace": str(tmp_path / "original")},
               "source": {"kind": "rest", "seeds": ["https://example.org/api"], "pagination": {"type": "cursor", "next_path": "$.next", "parameter": "cursor"}},
               "extract": {"mode": "json", "item_path": "$.items[*]", "fields": {"id": {"path": "id"}}}}
        path.write_text(yaml.safe_dump(raw), encoding="utf-8")
        cfg = load_config(path)
        proof = tmp_path / "proof.json"
        proof.write_text(json.dumps(trial_reference(cfg, {"status": "succeeded", "processed": 1},
            [{"request_fingerprint": "a" * 64, "content_sha256": "b" * 64}], [])), encoding="utf-8")
        template = tmp_path / "templates" / "captured.yaml"
        capture(cfg, proof, template, template_id="user/api")
        rendered = bundled_template_catalog([template.parent]).render("user/api", {"seed_url_1": f"http://127.0.0.1:{server.server_port}/items"})
        rendered["project"]["workspace"] = str(tmp_path / "new")
        rendered["http"].update(allow_private_network=True, delay_seconds=0)
        path = tmp_path / "new.yaml"
        path.write_text(yaml.safe_dump(rendered), encoding="utf-8")
        with Pipeline(load_config(path)) as pipeline:
            summary = pipeline.run()
            rows = pipeline.state.rows("SELECT data_json FROM records WHERE run_id=?", (summary["run_id"],))
        assert summary["status"] == "succeeded"
        assert sorted(json.loads(row["data_json"])["id"] for row in rows) == [1, 2]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
