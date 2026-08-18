"""S2.1.4：重试配置双轨合并（retry_max→retries 别名 + retry_on_status 生效）。

验收：用户配置的 retry_on_status / retry_max 生效；默认值统一到 DEFAULTS 单点
且与 RETRYABLE_STATUS 一致；0 可表示不重试。
"""

from __future__ import annotations

import urllib.error
from email.message import Message
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from omnicrawler.core.config import DEFAULTS, load_config
from omnicrawler.core.errors import ConfigParseError, PermanentFetchError
from omnicrawler.core.models import CrawlRequest
from omnicrawler.fetching.http_client import HTTPFetcher
from omnicrawler.fetching.retry import RETRYABLE_STATUS, parse_retry_config


def _config(tmp_path: Path, *, http=None) -> object:
    value = {
        "project": {"name": "retry-test", "workspace": str(tmp_path / "workspace")},
        "source": {"kind": "static_html", "seeds": ["https://example.org"]},
        "http": {
            "resolve_dns": False,
            "respect_robots": False,
            "delay_seconds": 0,
            **(http or {}),
        },
    }
    path = tmp_path / "retry.yaml"
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

    def open(self, request, timeout):
        outcome = next(self.outcomes)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _http_error(code, headers=None):
    message = Message()
    for key, value in (headers or {}).items():
        message[key] = value
    return urllib.error.HTTPError("https://example.org", code, "error", message, None)


def test_s214_retry_max_alias_merges_into_retries(tmp_path: Path) -> None:
    config = _config(tmp_path, http={"retry_max": 5})
    assert config.raw["http"]["retries"] == 5


def test_s214_retry_max_zero_disables_retry(tmp_path: Path) -> None:
    config = _config(tmp_path, http={"retry_max": 0})
    assert config.raw["http"]["retries"] == 0


def test_s214_retries_wins_over_retry_max(tmp_path: Path) -> None:
    config = _config(tmp_path, http={"retries": 2, "retry_max": 7})
    assert config.raw["http"]["retries"] == 2


@pytest.mark.parametrize("bad", [-1, "abc", True])
def test_s214_retry_max_invalid_fails_validation(tmp_path: Path, bad) -> None:
    with pytest.raises(ConfigParseError, match="http.retry_max"):
        _config(tmp_path, http={"retry_max": bad})


def test_s214_retry_max_not_reported_as_unknown_key(tmp_path: Path) -> None:
    config = _config(tmp_path, http={"retry_max": 3})
    assert not any("retry_max" in w for w in config.warnings)


def test_s214_default_statuses_consistent_with_retryable() -> None:
    assert set(DEFAULTS["http"]["retry_on_status"]) == set(RETRYABLE_STATUS)


def test_s214_parse_retry_config_defaults_and_custom() -> None:
    defaults = parse_retry_config({})
    assert defaults["status_codes"] == set(RETRYABLE_STATUS)
    assert defaults["max_retries"] == 3
    custom = parse_retry_config(
        {"retry_on_status": [500, "503"], "retries": 1, "retry_jitter": 0}
    )
    assert custom["status_codes"] == {500, 503}
    assert custom["max_retries"] == 1
    assert parse_retry_config({"retry_on_status": []})["status_codes"] == set()


def test_s214_fetch_retries_configured_status(tmp_path: Path) -> None:
    fetcher = HTTPFetcher(_config(tmp_path, http={"retries": 2, "retry_on_status": [500]}))
    fetcher.opener = _Opener([_http_error(500), _Response(b"after retry")])
    with patch("time.sleep"):
        assert fetcher.fetch(CrawlRequest("https://example.org")).body == b"after retry"


def test_s214_fetch_default_does_not_retry_501(tmp_path: Path) -> None:
    fetcher = HTTPFetcher(_config(tmp_path, http={"retries": 2}))
    fetcher.opener = _Opener([_http_error(501)])
    with patch("time.sleep"):
        with pytest.raises(PermanentFetchError, match="HTTP 501"):
            fetcher.fetch(CrawlRequest("https://example.org"))


def test_s214_fetch_retry_max_one_means_no_retry(tmp_path: Path) -> None:
    fetcher = HTTPFetcher(_config(tmp_path, http={"retry_max": 1}))
    fetcher.opener = _Opener([_http_error(429)])
    with patch("time.sleep"):
        with pytest.raises(PermanentFetchError, match="HTTP 429"):
            fetcher.fetch(CrawlRequest("https://example.org"))


def test_s214_fetch_retry_max_two_recovers(tmp_path: Path) -> None:
    fetcher = HTTPFetcher(_config(tmp_path, http={"retry_max": 2}))
    fetcher.opener = _Opener([_http_error(503, {"Retry-After": "0"}), _Response(b"recovered")])
    with patch("time.sleep"):
        assert fetcher.fetch(CrawlRequest("https://example.org")).body == b"recovered"


def test_b03_002_retry_after_capped_in_sync_fetcher(tmp_path: Path) -> None:
    """B03-002：同步 HTTPFetcher 的 Retry-After 必须封顶（默认 60s），防恶意头无限长睡。"""
    fetcher = HTTPFetcher(_config(tmp_path, http={"retries": 2, "retry_on_status": [503]}))
    fetcher.opener = _Opener([
        _http_error(503, {"Retry-After": "2147483647"}),
        _Response(b"after cap"),
    ])
    with patch("time.sleep") as sleeper:
        body = fetcher.fetch(CrawlRequest("https://example.org")).body
    assert body == b"after cap"
    waits = [call.args[0] for call in sleeper.call_args_list]
    assert waits and max(waits) <= 60.0


def test_b03_002_retry_after_cap_respects_custom_cap(tmp_path: Path) -> None:
    """B03-002：retry_after_cap_seconds 可自定义封顶值。"""
    fetcher = HTTPFetcher(_config(tmp_path, http={"retries": 2, "retry_on_status": [503], "retry_after_cap_seconds": 5}))
    fetcher.opener = _Opener([
        _http_error(503, {"Retry-After": "100"}),
        _Response(b"after custom cap"),
    ])
    with patch("time.sleep") as sleeper:
        body = fetcher.fetch(CrawlRequest("https://example.org")).body
    assert body == b"after custom cap"
    waits = [call.args[0] for call in sleeper.call_args_list]
    assert waits and max(waits) <= 5.0


def test_s214_fetch_empty_status_list_raises_immediately(tmp_path: Path) -> None:
    fetcher = HTTPFetcher(_config(tmp_path, http={"retries": 3, "retry_on_status": []}))
    fetcher.opener = _Opener([_http_error(429)])
    with patch("time.sleep"):
        with pytest.raises(PermanentFetchError, match="HTTP 429"):
            fetcher.fetch(CrawlRequest("https://example.org"))
