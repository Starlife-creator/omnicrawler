"""An unchanged ETag must not hide new extraction rules or cleaning dependencies."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.pipeline import Pipeline


@pytest.mark.parametrize("change", ["rule", "quality"])
def test_304_refreshes_body_when_extraction_dependency_changes(tmp_path, change):
    statuses = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            unchanged = self.headers.get("If-None-Match") == '"stable"'
            status = 304 if unchanged else 200
            statuses.append(status)
            body = b"" if unchanged else b'{"items":[{"id":1,"price":" 100 ","new_price":" 200 "}]}'
            self.send_response(status)
            self.send_header("ETag", '"stable"')
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    path = tmp_path / "task.yaml"
    path.write_text(yaml.safe_dump({
        "project":{"name":"dependencies","task_id":"dependencies","workspace":str(tmp_path/"work")},
        "source":{"kind":"rest","seeds":[f"http://127.0.0.1:{server.server_port}/items"]},
        "http":{"allow_private_network":True,"respect_robots":False,"delay_seconds":0,"retries":0},
        "crawl":{"max_pages":1,"allow_domains":["127.0.0.1"]},
        "extract":{"mode":"json","item_path":"$.items[*]","fields":{"id":{"path":"id"},"price":{"path":"price"}}},
        "updates":{"enabled":True,"identity_fields":["id"]},
        "quality":{"normalize":{"enabled":False}},
        "outputs":{"jsonl":True,"csv":True,"xlsx":False},
    }),encoding="utf-8")
    config = load_config(path)
    try:
        with Pipeline(config) as pipeline:
            first = pipeline.run()
            assert first["status"] == "succeeded"
        if change == "rule":
            config.raw["extract"]["fields"]["price"]["path"] = "new_price"
        else:
            config.raw["quality"]["normalize"]["enabled"] = True
        with Pipeline(config) as pipeline:
            second = pipeline.run()
            rows = pipeline.state.rows("SELECT data_json FROM records WHERE run_id=?",(second["run_id"],))
            assert second["status"] == "succeeded"
            assert len(rows) == 1
            price = json.loads(rows[0]["data_json"])["price"]
            assert str(price).strip() == ("200" if change == "rule" else "100")
            responses = pipeline.state.rows("SELECT status_code,request_fingerprint FROM responses WHERE run_id=? ORDER BY id",(second["run_id"],))
            assert [r["status_code"] for r in responses] == [304,200]
            assert len({r["request_fingerprint"] for r in responses}) == 1
        with Pipeline(config) as pipeline:
            third = pipeline.run()
            assert third["status"] == "succeeded"
        assert statuses == [200,304,200,304]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
