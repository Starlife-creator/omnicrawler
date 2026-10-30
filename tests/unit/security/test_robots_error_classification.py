"""A-P2-7：robots.txt 异常分类（DNS / 超时 / HTTP / 解析 / 过大 / 其它）回归测试。

- 异常类型分别落诊断记录与 egress 审计事件；
- ``fail_closed`` 语义不回归：fail_closed=True 时网络故障拒绝，False 时放行；
- 4xx 视为未配置 robots（允许），5xx 按 fail_closed 收敛。
"""
from __future__ import annotations

import socket
import urllib.error
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from omnicrawler.core.config import DEFAULTS, AppConfig
from omnicrawler.core.errors import ResponseTooLargeError
from omnicrawler.security.policy import RobotsPolicy, classify_robots_error


class _StubEgress:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, str, bool]] = []

    @contextmanager
    def request(self, _url: str, **_kwargs: Any):
        yield

    def record_response(self, _size: int, **_kwargs: Any) -> None:
        return None

    def record_robots_issue(self, kind: str, url: str, *, detail: str, allowed: bool) -> None:
        self.events.append((kind, url, detail, allowed))


class _StubResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self, _limit: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> _StubResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


class _StubOpener:
    def __init__(self, exc: Exception | None = None, body: bytes = b"") -> None:
        self.exc = exc
        self.body = body

    def open(self, _request: object, timeout: float | None = None) -> _StubResponse:
        if self.exc is not None:
            raise self.exc
        return _StubResponse(self.body)


def _policy(tmp_path: Path, *, fail_closed: bool, opener: _StubOpener, egress: _StubEgress | None = None) -> RobotsPolicy:
    raw = {
        "project": {"name": "robots", "workspace": "work"},
        "source": {"kind": "static_html", "seeds": ["https://example.com/start"]},
        "http": {**DEFAULTS["http"], "robots_fail_closed": fail_closed},
    }
    return RobotsPolicy(
        AppConfig(tmp_path / "task.yaml", tmp_path, raw, tmp_path / "work"),
        opener=opener,
        egress=egress,
    )


# ── 分类纯函数 ──────────────────────────────────────────────────────────

def test_classify_dns_failure() -> None:
    kind, detail = classify_robots_error(urllib.error.URLError(socket.gaierror("nodename")))
    assert kind == "dns"
    assert "DNS" in detail


def test_classify_timeout() -> None:
    kind, _detail = classify_robots_error(urllib.error.URLError(TimeoutError("timed out")))
    assert kind == "timeout"


def test_classify_http_error() -> None:
    kind, detail = classify_robots_error(
        urllib.error.HTTPError("https://x/robots.txt", 503, "busy", {}, None)
    )
    assert kind == "http" and "503" in detail


def test_classify_parse_error() -> None:
    kind, _detail = classify_robots_error(ValueError("bad robots line"))
    assert kind == "parse"


def test_classify_too_large() -> None:
    kind, _detail = classify_robots_error(ResponseTooLargeError("robots.txt超过配置的大小上限"))
    assert kind == "too_large"


def test_classify_other() -> None:
    kind, detail = classify_robots_error(RuntimeError("boom"))
    assert kind == "other" and "RuntimeError" in detail


# ── 策略集成：fail_closed 语义 + 审计事件 ────────────────────────────────

@pytest.mark.parametrize(
    ("exc", "kind"),
    [
        (urllib.error.URLError(socket.gaierror("nodename")), "dns"),
        (urllib.error.URLError(TimeoutError("timed out")), "timeout"),
        (ValueError("parse fail"), "parse"),
    ],
)
def test_network_failure_fail_closed_blocks(tmp_path: Path, exc: Exception, kind: str) -> None:
    egress = _StubEgress()
    policy = _policy(tmp_path, fail_closed=True, opener=_StubOpener(exc=exc), egress=egress)
    assert policy.allowed("https://example.com/private/") is False
    assert policy.last_error is not None
    assert policy.last_error["kind"] == kind
    assert policy.last_error["allowed"] is False
    assert egress.events and egress.events[0][0] == kind


@pytest.mark.parametrize(
    "exc",
    [urllib.error.URLError(socket.gaierror("nodename")), urllib.error.URLError(TimeoutError("x"))],
)
def test_network_failure_fail_open_allows(tmp_path: Path, exc: Exception) -> None:
    policy = _policy(tmp_path, fail_closed=False, opener=_StubOpener(exc=exc))
    assert policy.allowed("https://example.com/private/") is True
    assert policy.last_error is not None and policy.last_error["allowed"] is True


def test_http_4xx_allows_and_5xx_follows_fail_closed(tmp_path: Path) -> None:
    not_found = urllib.error.HTTPError("https://x/robots.txt", 404, "nope", {}, None)
    policy = _policy(tmp_path, fail_closed=True, opener=_StubOpener(exc=not_found))
    assert policy.allowed("https://example.com/") is True
    assert policy.last_error is not None and policy.last_error["kind"] == "http"

    busy = urllib.error.HTTPError("https://x/robots.txt", 503, "busy", {}, None)
    policy = _policy(tmp_path, fail_closed=True, opener=_StubOpener(exc=busy))
    assert policy.allowed("https://example.com/") is False

    policy_open = _policy(tmp_path, fail_closed=False, opener=_StubOpener(exc=busy))
    assert policy_open.allowed("https://example.com/") is True


def test_successful_robots_not_recorded_as_issue(tmp_path: Path) -> None:
    egress = _StubEgress()
    opener = _StubOpener(body=b"User-agent: *\nDisallow: /private/\n")
    policy = _policy(tmp_path, fail_closed=True, opener=opener, egress=egress)
    assert policy.allowed("https://example.com/private/") is False
    assert policy.last_error is None
    assert egress.events == []
