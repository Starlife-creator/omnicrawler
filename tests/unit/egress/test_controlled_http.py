from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.security import controlled_http


def test_scoped_network_config_restricts_endpoint_and_credentials(tmp_path: Path) -> None:
    config = controlled_http.scoped_network_config(
        "https://Api.Example.com:8443/v1/models?tenant=demo#ignored",
        workspace=tmp_path / "task",
        purpose="ai",
        timeout_seconds=12,
        max_response_bytes=4096,
    )

    assert config.workspace == (tmp_path / "task").resolve()
    assert config.section("source")["seeds"] == ["https://Api.Example.com:8443/v1/models?tenant=demo"]
    assert config.section("http")["proxy"] == ""
    assert config.section("http")["allow_private_network"] is False
    assert config.section("egress")["allowed_schemes"] == ["https"]
    assert config.section("egress")["allowed_domains"] == ["api.example.com"]
    assert config.section("egress")["allowed_ports"] == [8443]
    assert config.section("egress")["credential_domains"] == ["api.example.com"]
    assert config.section("egress")["credential_purposes"] == ["ai"]


@pytest.mark.parametrize(
    "endpoint",
    ["file:///private/data", "https://user:secret@example.com/v1", "not-a-url"],
)
def test_scoped_network_config_rejects_unsafe_endpoint(tmp_path: Path, endpoint: str) -> None:
    with pytest.raises(ValueError):
        controlled_http.scoped_network_config(endpoint, workspace=tmp_path, purpose="ai")


def test_scoped_fetch_uses_shared_http_fetcher(monkeypatch, tmp_path: Path) -> None:
    seen: dict[str, object] = {}

    class FakeFetcher:
        def __init__(self, config, *, purpose: str) -> None:
            seen["config"] = config
            seen["purpose"] = purpose

        def fetch(self, request: CrawlRequest) -> FetchResult:
            seen["request"] = request
            return FetchResult(request, request.url, 200, {}, b"{}", 0.01)

    monkeypatch.setattr(controlled_http, "HTTPFetcher", FakeFetcher)
    result = controlled_http.scoped_fetch(
        "https://api.example.com/v1/models",
        workspace=tmp_path,
        purpose="ai",
        method="POST",
        headers={"Authorization": "Bearer secret"},
        body=b"{}",
    )

    assert result.status == 200
    assert seen["purpose"] == "ai"
    request = seen["request"]
    assert isinstance(request, CrawlRequest)
    assert request.method == "POST"
    assert request.headers == {"Authorization": "Bearer secret"}
    config = seen["config"]
    assert config.section("egress")["credential_domains"] == ["api.example.com"]


def test_scoped_json_request_requires_a_json_object(monkeypatch, tmp_path: Path) -> None:
    request = CrawlRequest("https://api.example.com/v1/models")
    response = FetchResult(request, request.url, 200, {}, json.dumps({"data": []}).encode(), 0.01)
    monkeypatch.setattr(controlled_http, "scoped_fetch", lambda *_args, **_kwargs: response)

    assert controlled_http.scoped_json_request(request.url, workspace=tmp_path, purpose="ai") == {"data": []}

    invalid = FetchResult(request, request.url, 200, {}, b"[]", 0.01)
    monkeypatch.setattr(controlled_http, "scoped_fetch", lambda *_args, **_kwargs: invalid)
    with pytest.raises(RuntimeError, match="顶层必须是对象"):
        controlled_http.scoped_json_request(request.url, workspace=tmp_path, purpose="ai")
