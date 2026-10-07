"""Actual error responses reach explicit authentication checks on both transports."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.core.errors import PermanentFetchError, ResponseTooLargeError, SessionExpiredError
from omnicrawler.core.models import CrawlRequest
from omnicrawler.fetching.http_client import HTTPFetcher


@pytest.fixture
def denied_endpoint():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = b'<html><form class="login">Please login</form></html>' + b' ' * 2000
            self.send_response(401)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *_args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/"
    server.shutdown()
    server.server_close()
    thread.join(5)


@pytest.mark.parametrize("engine", ["urllib", "httpx_async"])
@pytest.mark.parametrize("check,expected", [
    ({"status_codes": [401], "selector": "form.login"}, SessionExpiredError),
    ({"status_codes": [401], "selector": "form.other"}, PermanentFetchError),
    ({}, PermanentFetchError),
])
def test_real_401_requires_all_explicit_auth_conditions(tmp_path, denied_endpoint, engine, check, expected):
    path = tmp_path / "auth.yaml"
    path.write_text(yaml.safe_dump({"project": {"workspace": "work"},
        "source": {"seeds": [denied_endpoint], "auth_check": check},
        "http": {"allow_private_network": True, "respect_robots": False, "delay_seconds": 0, "retries": 0}}), encoding="utf8")
    config = load_config(path)
    if engine == "httpx_async":
        pytest.importorskip("httpx")
        from omnicrawler.fetching.async_fetcher import HTTPXAsyncFetcher
        fetcher = HTTPXAsyncFetcher(config)
    else:
        fetcher = HTTPFetcher(config)
    try:
        with pytest.raises(expected):
            fetcher.fetch(CrawlRequest(denied_endpoint))
    finally:
        getattr(fetcher, "close", lambda: None)()


@pytest.mark.parametrize("engine", ["urllib", "httpx_async"])
def test_error_response_auth_check_keeps_size_limit(tmp_path, denied_endpoint, engine):
    path = tmp_path / "small.yaml"
    path.write_text(yaml.safe_dump({"project": {"workspace": "work"},
        "source": {"seeds": [denied_endpoint], "auth_check": {"status_codes": [401]}},
        "http": {"allow_private_network": True, "delay_seconds": 0, "max_response_bytes": 1024, "retries": 0}}), encoding="utf8")
    config = load_config(path)
    if engine == "httpx_async":
        pytest.importorskip("httpx")
        from omnicrawler.fetching.async_fetcher import HTTPXAsyncFetcher
        fetcher = HTTPXAsyncFetcher(config)
    else:
        fetcher = HTTPFetcher(config)
    try:
        with pytest.raises(ResponseTooLargeError):
            fetcher.fetch(CrawlRequest(denied_endpoint))
    finally:
        getattr(fetcher, "close", lambda: None)()
