from __future__ import annotations

import urllib.request

from omnicrawler.fetching.http_client import ExplicitProxyHandler


def test_explicit_proxy_ignores_system_bypass_and_keeps_credentials_on_proxy(monkeypatch):
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda _host: True)
    handler = ExplicitProxyHandler({"http": "http://user:pass@proxy.example:8080"})
    request = urllib.request.Request("http://origin.example/page")
    assert handler.proxy_open(request, "http://user:pass@proxy.example:8080", "http") is None
    assert request.host == "proxy.example:8080"
    assert request.has_proxy()
    assert request.get_header("Proxy-authorization") == "Basic dXNlcjpwYXNz"


def test_explicit_proxy_https_preserves_origin_for_tunnel(monkeypatch):
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda _host: True)
    handler = ExplicitProxyHandler({"https": "http://proxy.example:8080"})
    request = urllib.request.Request("https://origin.example/page")
    assert handler.proxy_open(request, "http://proxy.example:8080", "https") is None
    assert request.host == "proxy.example:8080"
    assert request._tunnel_host == "origin.example"
