from __future__ import annotations

import gzip
import urllib.error
import zlib
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.core.errors import PermanentFetchError, ResponseTooLargeError
from omnicrawler.core.models import CrawlRequest
from omnicrawler.fetching.http_client import HTTPFetcher, build_safe_opener, encode_request_payload


def _config(tmp_path: Path, *, http=None, login=None):
    value = {
        "project": {"name": "http-test", "workspace": str(tmp_path / "workspace")},
        "source": {"kind": "static_html", "seeds": ["https://example.org"]},
        "http": {
            "resolve_dns": False,
            "respect_robots": False,
            "delay_seconds": 0,
            "retries": 1,
            "max_response_bytes": 1024,
        },
    }
    if http:
        value["http"].update(http)
    if login:
        value["source"]["login"] = login
    path = tmp_path / "http.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return load_config(path)


class _Response:
    def __init__(self, body=b"ok", *, status=200, headers=None, url="https://example.org/final"):
        self._body = body
        self.status = status
        self.headers = headers or {"Content-Type": "text/plain"}
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, maximum):
        return self._body[:maximum]

    def geturl(self):
        return self._url


class _Opener:
    def __init__(self, outcomes):
        self.outcomes = iter(outcomes)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _http_error(code, headers=None):
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError("https://example.org", code, "error", message, None)


def test_fetch_success_gzip_headers_and_final_url(tmp_path: Path) -> None:
    payload = gzip.compress(b"decoded body")
    fetcher = HTTPFetcher(_config(tmp_path))
    fetcher.opener = _Opener(
        [
            _Response(
                payload,
                headers={
                    "Content-Type": "text/plain",
                    "Content-Encoding": "gzip",
                    "Content-Length": str(len(payload)),
                },
            )
        ]
    )
    result = fetcher.fetch(CrawlRequest("https://example.org/start", headers={"X-Test": "yes"}))
    assert result.body == b"decoded body"
    assert result.final_url == "https://example.org/final"
    request = fetcher.opener.requests[0][0]
    assert request.headers["X-test"] == "yes"
    # S2.5.6：基础编码始终声明；br/zstd 仅当对应解码库已安装时附加
    accept = request.headers["Accept-encoding"].split(", ")
    assert accept[0:2] == ["gzip", "deflate"]
    assert set(accept[2:]) <= {"br", "zstd"}


def test_response_declared_streamed_and_decompressed_size_limits(tmp_path: Path) -> None:
    fetcher = HTTPFetcher(_config(tmp_path))
    fetcher.opener = _Opener([_Response(b"small", headers={"Content-Length": "2048"})])
    with pytest.raises(ResponseTooLargeError, match="2048"):
        fetcher.fetch(CrawlRequest("https://example.org"))

    fetcher.opener = _Opener([_Response(b"x" * 1025)])
    with pytest.raises(ResponseTooLargeError):
        fetcher.fetch(CrawlRequest("https://example.org"))

    compressed = gzip.compress(b"x" * 2000)
    fetcher.opener = _Opener([_Response(compressed, headers={"Content-Encoding": "gzip"})])
    with pytest.raises(ResponseTooLargeError):
        fetcher.fetch(CrawlRequest("https://example.org"))

    with pytest.raises(ValueError, match="不完整"):
        HTTPFetcher._bounded_decompress(zlib.compress(b"valid")[:-2], zlib.MAX_WBITS, 1024)


def test_304_permanent_retryable_and_transport_retry(tmp_path: Path) -> None:
    fetcher = HTTPFetcher(_config(tmp_path))
    fetcher.opener = _Opener([_http_error(304, {"ETag": "v1"})])
    result = fetcher.fetch(CrawlRequest("https://example.org"))
    assert result.status == 304 and result.meta["not_modified"] is True
    assert result.headers["etag"] == "v1"

    fetcher.opener = _Opener([_http_error(404)])
    with pytest.raises(PermanentFetchError, match="HTTP 404"):
        fetcher.fetch(CrawlRequest("https://example.org"))

    retrying = HTTPFetcher(_config(tmp_path, http={"retries": 2, "retry_jitter": 0}))
    retrying.opener = _Opener([_http_error(503, {"Retry-After": "0"}), _Response(b"after retry")])
    with patch("time.sleep"):
        assert retrying.fetch(CrawlRequest("https://example.org")).body == b"after retry"

    transport = HTTPFetcher(_config(tmp_path, http={"retries": 2, "retry_jitter": 0}))
    transport.opener = _Opener(
        [urllib.error.URLError("temporary"), _Response(b"transport recovered")]
    )
    with patch("time.sleep"):
        assert transport.fetch(CrawlRequest("https://example.org")).body == b"transport recovered"


def test_login_runs_once_and_encodes_form(tmp_path: Path) -> None:
    config = _config(
        tmp_path,
        login={
            "url": "https://example.org/login",
            "method": "POST",
            "content_type": "application/x-www-form-urlencoded",
            "fields": {"username": "demo", "password": "value"},
            "headers": {"X-Login": "yes"},
        },
    )
    fetcher = HTTPFetcher(config)
    opener = _Opener([_Response(b"logged in"), _Response(b"page"), _Response(b"page two")])
    fetcher.opener = opener
    assert fetcher.fetch(CrawlRequest("https://example.org/page")).body == b"page"
    assert fetcher.fetch(CrawlRequest("https://example.org/page2")).body == b"page two"
    assert len(opener.requests) == 3
    login_request = opener.requests[0][0]
    assert login_request.full_url == "https://example.org/login"
    assert b"username=demo" in login_request.data


def test_payload_helpers_and_safe_opener_proxy_boundary(tmp_path: Path) -> None:
    assert encode_request_payload("GET", None, "application/json") == (None, {})
    body, headers = encode_request_payload("POST", {"id": 1}, "application/json")
    assert body == b'{"id": 1}' and headers["Content-Type"] == "application/json"
    body, headers = encode_request_payload("POST", {"tag": ["a", "b"]}, "form")
    assert body == b"tag=a&tag=b"
    assert headers["Content-Type"] == "application/x-www-form-urlencoded"

    direct = build_safe_opener(_config(tmp_path))
    assert direct is not None
    proxy_config = _config(tmp_path, http={"proxy": "https://proxy.example:8443"})
    proxy = build_safe_opener(proxy_config)
    assert proxy is not None
