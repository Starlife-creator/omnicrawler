"""AI provider 健壮性回归（C5/C6/C7/C8/C10）。

覆盖审计条目：
- C8  base_url 必须是合法 http(s) URL，否则构造期 ValueError
- C10 单次最大 token 写入请求体 payload
- C5  捕获 socket.timeout / URLError 等，不再诡异逃逸
- C6  HTTPError 透出响应体前 ~1KB 与中文处置建议
- C7  网络瞬断按指数退避重试；最终失败仍抛出可读错误
"""

from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from omnicrawl.core.config import AppConfig
from omnicrawl.services import ai_providers
from omnicrawl.services.ai_providers import AI_RETRY_ATTEMPTS, OpenAICompatibleProvider


class _FakeEgress:
    policy = None

    def request(self, url, *, purpose="ai", headers=None):
        from contextlib import nullcontext

        return nullcontext()

    def record_response(self, size: int, *, cost: float = 0.0, url: str = "") -> None:
        pass


class _FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *args) -> bool:
        return False

    def read(self, maximum: int) -> bytes:
        return self._body

    def geturl(self) -> str:
        return "https://api.example.com/v1/chat/completions"


def _make_provider(max_tokens: int | None = None) -> OpenAICompatibleProvider:
    config = {
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-x",
        "model": "gpt-x",
        "timeout_seconds": 5,
    }
    provider = OpenAICompatibleProvider("default", config, max_tokens=max_tokens)
    raw = {
        "project": {"name": "test"},
        "source": {"kind": "crawl"},
        "http": {"max_response_bytes": 1_000_000},
    }
    provider.app_config = AppConfig(Path("x.yaml"), Path.cwd(), raw, Path.cwd(), ())
    provider.egress = _FakeEgress()  # type: ignore[assignment]
    return provider


@pytest.mark.parametrize("bad_url", ["", "not-a-url", "ftp://example.com/x", "http://", "example.com/v1"])
def test_c8_invalid_base_url_rejected(bad_url: str) -> None:
    config = {"base_url": bad_url, "api_key": "sk", "model": "m"}
    with pytest.raises(ValueError, match="合法的 http"):
        OpenAICompatibleProvider("p", config)


def test_c10_max_tokens_written_into_payload() -> None:
    captured: dict[str, object] = {}
    provider = _make_provider(max_tokens=512)

    class _CaptureOpener:
        def open(self, request, timeout=None):
            captured["body"] = json.loads(request.data.decode("utf-8"))
            return _FakeResponse(b'{"choices":[{"message":{"content":"ok"}}],"usage":{}}')

    with patch("omnicrawl.services.ai_providers.build_safe_opener", return_value=_CaptureOpener()):
        provider.generate([{"role": "user", "content": "hi"}])
    assert captured["body"].get("max_tokens") == 512


def test_c6_http_error_surfaces_status_and_body(monkeypatch) -> None:
    monkeypatch.setattr(ai_providers.time, "sleep", lambda *_: None)
    provider = _make_provider()
    fp = io.BytesIO(b'{"error":"quota exceeded for sk"}')

    class _ErrOpener:
        def open(self, request, timeout=None):
            raise urllib.error.HTTPError(
                url="https://api.example.com/v1/chat/completions",
                code=429,
                msg="Too Many Requests",
                hdrs=None,
                fp=fp,
            )

    with patch("omnicrawl.services.ai_providers.build_safe_opener", return_value=_ErrOpener()):
        with pytest.raises(RuntimeError, match="HTTP 429"):
            provider.generate([{"role": "user", "content": "hi"}])


def test_c5_socket_timeout_is_caught_and_surfaced(monkeypatch) -> None:
    monkeypatch.setattr(ai_providers.time, "sleep", lambda *_: None)
    provider = _make_provider()
    calls = {"n": 0}

    class _TimeoutOpener:
        def open(self, request, timeout=None):
            calls["n"] += 1
            raise TimeoutError("timed out")

    with patch("omnicrawl.services.ai_providers.build_safe_opener", return_value=_TimeoutOpener()):
        with pytest.raises(RuntimeError, match="超时"):
            provider.generate([{"role": "user", "content": "hi"}])
    # C7：超时按指数退避重试至耗尽（不立即抛、也不无限）
    assert calls["n"] == AI_RETRY_ATTEMPTS


def test_c7_retry_then_success(monkeypatch) -> None:
    monkeypatch.setattr(ai_providers.time, "sleep", lambda *_: None)
    provider = _make_provider()
    calls = {"n": 0}

    class _FlakyOpener:
        def open(self, request, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise urllib.error.URLError(reason=ConnectionError("conn refused"))
            return _FakeResponse(b'{"choices":[{"message":{"content":"ok"}}],"usage":{"total_tokens":1}}')

    with patch("omnicrawl.services.ai_providers.build_safe_opener", return_value=_FlakyOpener()):
        result = provider.generate([{"role": "user", "content": "hi"}])
    assert result.text == "ok"
    assert calls["n"] == 2  # 第一次失败，重试成功


def test_c5_non_json_response_surfaced_with_guidance(monkeypatch) -> None:
    monkeypatch.setattr(ai_providers.time, "sleep", lambda *_: None)
    provider = _make_provider()

    class _HtmlOpener:
        def open(self, request, timeout=None):
            return _FakeResponse(b"<html>502 Bad Gateway</html>")

    with patch("omnicrawl.services.ai_providers.build_safe_opener", return_value=_HtmlOpener()):
        with pytest.raises(RuntimeError, match="不是合法 JSON"):
            provider.generate([{"role": "user", "content": "hi"}])
