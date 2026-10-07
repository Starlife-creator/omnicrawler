import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.pipeline import Pipeline

pytestmark = pytest.mark.skipif(os.environ.get("OMNICRAWL_BROWSER_TESTS") != "1", reason="requires local Chromium")


def test_real_pipeline_collects_virtual_records_and_waits_for_data(tmp_path):
    html = """<html><body><div id="list" style="height:100px;overflow:auto"></div>
    <div id="end" style="display:none">End</div><script>
    const list=document.querySelector('#list'); let batch=0;
    function render(){list.innerHTML=[batch*2+1,batch*2+2].map(i=>
      `<article data-id="${i}" style="height:160px"><h2>Item ${i}</h2></article>`).join('');}
    setTimeout(render,150);
    setInterval(()=>{if(batch===0 && list.scrollTop>0){batch=1;render();document.querySelector('#end').style.display='block';}},20);
    </script></body></html>"""
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(html.encode())
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump({
        "project": {"name": "virtual", "workspace": str(tmp_path / "work")},
        "source": {"kind": "browser", "seeds": [f"http://127.0.0.1:{server.server_port}/"]},
        "http": {"allow_private_network": True, "respect_robots": False, "delay_seconds": 0},
        "crawl": {"max_depth": 0},
        "browser": {"wait_until": "domcontentloaded", "readiness": {"selector": "article", "min_count": 2, "stable_ms": 0},
                    "collection": {"item_selector": "article", "container_selector": "#list", "identity_attribute": "data-id",
                                   "end_selector": "#end", "pause_ms": 100, "max_steps": 10}},
        "extract": {"mode": "html", "item_selector": "article", "fields": {"title": {"selector": "h2"}}},
    }), encoding="utf-8")
    try:
        with Pipeline(load_config(path)) as pipeline:
            summary = pipeline.run()
            rows = pipeline.state.rows("SELECT data_json FROM records WHERE run_id=?", (summary["run_id"],))
            import json
            assert {json.loads(row["data_json"])["title"] for row in rows} == {"Item 1", "Item 2", "Item 3", "Item 4"}
            checkpoints = pipeline.state.rows("SELECT status FROM stage_checkpoints WHERE run_id=? AND stage='discover'", (summary["run_id"],))
            assert checkpoints and all(row["status"] == "succeeded" for row in checkpoints)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
