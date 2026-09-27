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

from omnicrawler.core.config import DEFAULTS, AppConfig
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.fetching.tls_impersonator import (
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
        # ★ `_choose_impersonate` 无条件 `from curl_cffi import BrowserType`；生产侧由
        #   `TLSImpersonator.__post_init__` 的 ImportError 兜底挡住（未安装即回退 httpx），
        #   所以 helper 本身不必兜底。但本用例**直接**调该 helper，缺可选依赖时
        #   没有拦截点 ⇒ base 安装下必红。降级路径由 test_unavailable_without_curl_cffi
        #   （mock sys.modules）单独守卫，本用例只在依赖齐全时才测"择优"语义。
        pytest.importorskip("curl_cffi")
        chosen = _choose_impersonate(DEFAULT_IMPERSONATE)
        assert chosen.startswith("chrome")


class TestResolveOverride:
    def test_resolve_override_shape(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setitem(__import__("sys").modules, "curl_cffi", None)
        config = _config(tmp_path)
        fake = _FakeFetcher(_fake_result(CrawlRequest("https://example.com")))
        imp = TLSImpersonator(config, fake)
        # B13-005：mock 固定 IPv6 地址，避免本机真实 DNS 返回的地址族影响断言；
        # 同时验证 IPv6 地址被 [ ] 括号包裹（curl --resolve 语义）。
        monkeypatch.setattr(
            imp.target_policy, "approved_addresses", lambda _h, _p: ("2606:4700::1111",)
        )
        overrides = imp._resolve_override("https://example.com/path")
        assert isinstance(overrides, list)
        assert overrides is not None and len(overrides) >= 1
        entry = overrides[0]
        assert isinstance(entry, bytes)
        host, port, address = entry.decode("ascii").split(":", 2)
        assert host == "example.com"
        assert port == "443"
        assert address == "[2606:4700::1111]"


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
        # ★ 抑制规则必须在**函数体内**、importorskip **之后**建立，不能用
        #   `@pytest.mark.filterwarnings(...curl_cffi.utils.CurlCffiWarning)`：
        #   标记里的点路径由 pytest 在 runtest 协议阶段用 importlib 解析，那发生在
        #   函数体之外 ⇒ 缺 curl_cffi（base 安装）时抛 ModuleNotFoundError，
        #   importorskip 拦不住 ⇒ 整个 pytest run 以 INTERNALERROR 中断，
        #   后续上千条用例**从未执行**。基线安装无 curl_cffi 时本用例本就该 skip，
        #   所以把过滤移进体内不改变任何断言语义。
        pytest.importorskip("curl_cffi")
        import warnings

        from curl_cffi.utils import CurlCffiWarning

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
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Proactor event loop does not implement add_reader",
                category=CurlCffiWarning,
            )
            result = asyncio.run(imp.fetch_async(CrawlRequest(local_server)))
        assert result.status == 200
        assert b"tls-smoke" in result.body
        assert fake.fetch_called == 0  # 真实走了 curl_cffi，未降级
