import os
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

from omnicrawl.core.config import load_config
from omnicrawl.core.models import CrawlRequest
from omnicrawl.fetching.browser_fetcher import BrowserFetcher

pytestmark = pytest.mark.skipif(
    os.environ.get("OMNICRAWL_BROWSER_TESTS") != "1",
    reason="set OMNICRAWL_BROWSER_TESTS=1 after installing Playwright Chromium",
)


class _DynamicHandler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path == "/api/items":
            body = b'{"data":{"name":"Captured API value"}}'
            content_type = "application/json"
        else:
            body = b"""<!doctype html><html><body><div id="root">Loading</div>
<script>
fetch('/api/items').then(r => r.json()).then(data => {
  document.querySelector('#root').textContent = data.data.name;
  document.querySelector('#root').setAttribute('data-ready', 'yes');
});
</script></body></html>"""
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def test_real_playwright_pool_dynamic_render_and_api_capture():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DynamicHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "project.yaml"
            url = f"http://127.0.0.1:{server.server_port}/"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "project": {"name": "browser", "workspace": str(root / "work")},
                        "source": {"kind": "browser", "seeds": [url]},
                        "http": {
                            "allow_private_network": True,
                            "respect_robots": False,
                            "delay_seconds": 0,
                            "timeout_seconds": 15,
                        },
                        "browser": {
                            "engine": "playwright",
                            "headless": True,
                            "pool_size": 1,
                            "wait_until": "networkidle",
                            "actions": [
                                {"action": "wait_for", "selector": "#root[data-ready='yes']"}
                            ],
                        },
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            fetcher = BrowserFetcher(load_config(config_path))
            try:
                first = fetcher.fetch(CrawlRequest(url, render=True))
                second = fetcher.fetch(CrawlRequest(url, render=True))
            finally:
                fetcher.close()

            assert b"Captured API value" in first.body
            captured = [
                item for item in first.meta["api_responses"]
                if item["url"].endswith("/api/items")
            ]
            assert captured[0]["json"]["data"]["name"] == "Captured API value"
            assert b"Captured API value" in second.body
    finally:
        server.shutdown()
        server.server_close()


def test_real_selenium_dynamic_render_and_action_wait():
    if not os.environ.get("OMNICRAWL_SELENIUM_DRIVER"):
        pytest.skip("OMNICRAWL_SELENIUM_DRIVER is not configured")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _DynamicHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config_path = root / "project.yaml"
            url = f"http://127.0.0.1:{server.server_port}/"
            config_path.write_text(
                yaml.safe_dump(
                    {
                        "project": {"name": "selenium", "workspace": str(root / "work")},
                        "source": {"kind": "browser", "seeds": [url]},
                        "http": {"allow_private_network": True, "respect_robots": False, "delay_seconds": 0},
                        "egress": {"allow_unintercepted_selenium": True},
                        "browser": {
                            "engine": "selenium",
                            "headless": True,
                            "launch_args": ["--no-sandbox", "--disable-dev-shm-usage"],
                            "actions": [{"action": "wait_for", "selector": "#root[data-ready='yes']"}],
                        },
                    },
                    sort_keys=False,
                ),
                encoding="utf-8",
            )
            fetcher = BrowserFetcher(load_config(config_path))
            try:
                result = fetcher.fetch(CrawlRequest(url, render=True))
            finally:
                fetcher.close()
            assert b"Captured API value" in result.body
    finally:
        server.shutdown()
        server.server_close()
