"""Real Selenium navigation must progress with mandatory request interception."""
from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest
from omnicrawler.fetching.browser_fetcher import BrowserFetcher

pytestmark = pytest.mark.skipif(
    os.environ.get("OMNICRAWL_BROWSER_TESTS") != "1",
    reason="set OMNICRAWL_BROWSER_TESTS=1 with an installed Chrome and driver",
)


@pytest.mark.parametrize("scenario", ["plain", "redirect", "actions", "actions-no-wait", "actions-role", "blocked", "session", "timeout"])
def test_selenium_navigation_without_fallback(tmp_path, monkeypatch, scenario):
    if not os.environ.get("OMNICRAWL_SELENIUM_DRIVER"):
        pytest.skip("OMNICRAWL_SELENIUM_DRIVER is not configured")
    import psutil
    from selenium import webdriver

    hits = []
    session_seen = []
    release = threading.Event()
    owned = []
    original = webdriver.Chrome

    def launch(**kwargs):
        driver = original(**kwargs)
        owned.append(psutil.Process(driver.service.process.pid))
        owned.extend(owned[-1].children(recursive=True))
        return driver

    monkeypatch.setattr(webdriver, "Chrome", launch)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            hits.append(self.path)
            if self.path == "/timeout":
                release.wait(15)
                return
            if self.path == "/session":
                session_seen.append("owned_session=fixture" in self.headers.get("Cookie", ""))
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/plain")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path.startswith("/actions"):
                body = b'''<html><body><input id="value"><input type="checkbox" id="checked">
<select id="choice"><option value="1">one</option><option value="0">zero</option></select><button id="go"
onclick="location.href='/next?value='+encodeURIComponent(document.querySelector('#value').value)
+'&checked='+document.querySelector('#checked').checked+'&choice='+document.querySelector('#choice').value">go</button></body></html>'''
            elif self.path == "/blocked":
                body = (f'''<html><body><img src="http://localhost:{self.server.server_port}/forbidden">
<div id="ready">owned-selenium-success</div></body></html>''').encode()
            else:
                body = b'<html><body><div id="ready">owned-selenium-success</div></body></html>'
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            if self.path == "/session":
                self.send_header("Set-Cookie", "owned_session=fixture; Path=/; HttpOnly; Max-Age=3600")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/{scenario}"
    actions = ([{"action": "fill", "selector": "#value", "value": "0 False"},
                {"action": "click", "selector": "#go"},
                {"action": "wait_for", "selector": "#ready", "timeout_ms": 5000}]
               if scenario.startswith("actions") else [])
    if scenario == "actions-no-wait":
        actions.pop()
    if scenario == "actions-role":
        actions = [actions[0], {"action": "press", "selector": "#value", "key": "End"},
                   {"action": "select_option", "selector": "#choice", "value": 0},
                   {"action": "check", "selector": "#checked"},
                   {"action": "scroll_bottom", "times": 1, "pause_ms": 0},
                   {"action": "click", "role": "button", "name": "go"},
                   {"action": "wait_for_url", "value": "**/next?*"}, actions[-1]]
    path = tmp_path / "task.yaml"
    path.write_text(yaml.safe_dump({
        "project": {"name": "selenium-navigation", "workspace": str(tmp_path / "work")},
        "source": {"kind": "browser", "seeds": [url]},
        "http": {"allow_private_network": True, "respect_robots": False,
                 "delay_seconds": 0, "selenium_watchdog_seconds": 3 if scenario == "timeout" else 15},
        "egress": {"allowed_domains": ["127.0.0.1"], "audit": True},
        "browser": {"engine": "selenium", "headless": True, "actions": actions,
                    "persist_profile": scenario == "session",
                    "launch_args": ["--no-sandbox", "--disable-dev-shm-usage"]},
    }), encoding="utf-8")
    config = load_config(path)
    fetcher = BrowserFetcher(config)
    started = time.monotonic()
    try:
        if scenario == "timeout":
            from omnicrawler.fetching.browser_fetcher import SeleniumRuntimeUnavailableError
            with pytest.raises(SeleniumRuntimeUnavailableError):
                fetcher.fetch(CrawlRequest(url, render=True))
            assert time.monotonic() - started < 12
            return
        result = fetcher.fetch(CrawlRequest(url, render=True))
        assert b"owned-selenium-success" in result.body
        assert "renderer_fallback" not in result.meta
        if scenario == "session":
            again = fetcher.fetch(CrawlRequest(url, render=True))
            assert b"owned-selenium-success" in again.body and "renderer_fallback" not in again.meta
            assert session_seen == [False, True]
        if scenario.startswith("actions"):
            assert any(hit.startswith("/next?value=0%20False") for hit in hits)
            if scenario == "actions-role":
                assert any("checked=true&choice=0" in hit for hit in hits)
        if scenario == "blocked":
            assert "/forbidden" not in hits
            audit = [json.loads(line) for line in fetcher.egress.audit_path.read_text(encoding="utf-8").splitlines()]
            assert any(row["event"] == "blocked" and "/forbidden" in row["url"] for row in audit)
    finally:
        fetcher.close()
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert owned and not any(proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE for proc in owned)
