"""Tests for fetching.tls_impersonator — TLS 指纹伪装层。

用本地 HTTP 服务器验证真实 curl_cffi 抓取（含 RESOLVE 钉扎），
无 curl_cffi 时验证降级路径；不依赖外部网络。
"""

from __future__ import annotations

import asyncio
import copy
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from omnicrawl.core.config import DEFAULTS, AppConfig
from omnicrawl.core.models import CrawlRequest, FetchResult
from omnicrawl.fetching.tls_impersonator import (
    DEFAULT_IMPERSONATE,
    TLSImpersonator,
    _choose_impersonate,
)


def _config(tmp_path: Path) -> AppConfig:
    raw = copy.deepcopy(DEFAULTS)
    raw["project"] = {"name": "tls-test", "workspace": str(tmp_path / "ws")}
    raw["source"] = {"kind": "static_html", "seeds": ["https://example.com"]}
    raw["http"]["allow_private_network"] = True
    path = tmp_path / "config.yaml"
    path.write_text("project:\n  name: tls-test\n", encoding="utf-8")
    return AppConfig(path, tmp_path, raw, tmp_path / "ws")


class _FakeFetcher:
    def __init__(self, result: FetchResult | Exception) -> None:
        self.result = result
        self.fetch_called = 0

    async def fetch_many(self, requests: list[CrawlRequest]) -> list[FetchResult | Exception]:
        self.fetch_called += 1
        return [self.result]

    def fetch(self, request: CrawlRequest) -> FetchResult:
        self.fetch_called += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _fake_result(request: CrawlRequest) -> FetchResult:
    return FetchResult(request, request.url, 200, {"content-type": "text/html"}, b"<html>ok</html>", 0.1)


class TestFallbackWithoutCurlCffi:
    def test_unavailable_without_curl_cffi(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setitem(__import__("sys").modules, "curl_cffi", None)
        config = _config(tmp_path)
        fake = _FakeFetcher(_fake_result(CrawlRequest("https://example.com")))
        imp = TLSImpersonator(config, fake)
        assert imp.available is False

    def test_fetch_async_falls_back(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setitem(__import__("sys").modules, "curl_cffi", None)
        config = _config(tmp_path)
        request = CrawlRequest("https://example.com")
        fake = _FakeFetcher(_fake_result(request))
        imp = TLSImpersonator(config, fake)
        import asyncio

        fetched = asyncio.run(imp.fetch_async(request))
        assert fetched.status == 200
        assert fake.fetch_called == 1

    def test_fetch_sync_falls_back(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setitem(__import__("sys").modules, "curl_cffi", None)
        config = _config(tmp_path)
        request = CrawlRequest("https://example.com")
        fake = _FakeFetcher(_fake_result(request))
        imp = TLSImpersonator(config, fake)
        fetched = imp.fetch(request)
        assert fetched.final_url == request.url
        assert fake.fetch_called == 1

    def test_fallback_error_propagates(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setitem(__import__("sys").modules, "curl_cffi", None)
        config = _config(tmp_path)
        request = CrawlRequest("https://example.com")
        fake = _FakeFetcher(RuntimeError("network down"))
        imp = TLSImpersonator(config, fake)
        with pytest.raises(RuntimeError, match="network down"):
            imp.fetch(request)


class TestChooseImpersonate:
    def test_preferred_used_when_available(self) -> None:
        chosen = _choose_impersonate(DEFAULT_IMPERSONATE)
        assert chosen.startswith("chrome")


class TestResolveOverride:
    def test_resolve_override_shape(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setitem(__import__("sys").modules, "curl_cffi", None)
        config = _config(tmp_path)
        fake = _FakeFetcher(_fake_result(CrawlRequest("https://example.com")))
        imp = TLSImpersonator(config, fake)
        overrides = imp._resolve_override("https://example.com/path")
        assert isinstance(overrides, list)
        assert overrides is not None and len(overrides) >= 1
        entry = overrides[0]
        assert isinstance(entry, bytes)
        host, port, _address = entry.decode("ascii").rsplit(":", 2)
        assert host == "example.com"
        assert port == "443"


class TestRealCurlCffiFetch:
    """本地 HTTP 服务器上的真实抓取（curl_cffi 可用时）。"""

    @pytest.fixture()
    def local_server(self):
        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                body = b"<html><body><h1>tls-smoke</h1></body></html>"
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args) -> None:
                pass

        server = HTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        yield f"http://127.0.0.1:{server.server_address[1]}/"
        server.shutdown()
        thread.join(timeout=5)

    def test_impersonate_fetch_local(self, tmp_path: Path, local_server: str) -> None:
        curl_cffi = pytest.importorskip("curl_cffi")
        raw = copy.deepcopy(DEFAULTS)
        raw["project"] = {"name": "tls-live", "workspace": str(tmp_path / "ws")}
        raw["source"] = {"kind": "static_html", "seeds": [local_server]}
        raw["http"]["allow_private_network"] = True
        raw["http"]["resolve_dns"] = False
        path = tmp_path / "config.yaml"
        path.write_text("project:\n  name: tls-live\n", encoding="utf-8")
        config = AppConfig(path, tmp_path, raw, tmp_path / "ws")
        fake = _FakeFetcher(_fake_result(CrawlRequest(local_server)))
        imp = TLSImpersonator(config, fake)
        assert imp.available is True
        result = asyncio.run(imp.fetch_async(CrawlRequest(local_server)))
        assert result.status == 200
        assert b"tls-smoke" in result.body
        assert fake.fetch_called == 0  # 真实走了 curl_cffi，未降级
