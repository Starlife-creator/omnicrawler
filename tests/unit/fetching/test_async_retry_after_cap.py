"""S2.5.9：async Retry-After 封顶（不再静默睡 2 小时）。"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from omnicrawl.core.config import load_config
from omnicrawl.core.errors import PermanentFetchError
from omnicrawl.core.models import CrawlRequest
from omnicrawl.fetching.async_fetcher import HTTPXAsyncFetcher
from omnicrawl.fetching.retry import retry_after_seconds


def _config(tmp_path: Path) -> Path:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: s259, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n"
        "http: {retries: 3}\n",
        encoding="utf-8",
    )
    return config_path


class _Client:
    def __init__(self, retry_after: str) -> None:
        self._retry_after = retry_after

    def stream(self, _method, _url, **_kwargs):
        class _Stream:
            status_code = 429
            url = "https://example.org/"

            def __init__(self, headers: dict) -> None:
                self.headers = headers

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            def raise_for_status(self) -> None:
                raise httpx.HTTPStatusError(
                    "429", request=SimpleNamespace(url="https://example.org/"),
                    response=SimpleNamespace(
                        status_code=429, headers=self.headers, url=self.url,
                    ),
                )

            async def __aiter__(self):
                if False:  # pragma: no cover - raise_for_status fires first
                    yield b""

        return _Stream({"Retry-After": self._retry_after})


def _fetcher(tmp_path: Path, *, cap: float | None = None) -> HTTPXAsyncFetcher:
    path = _config(tmp_path)
    if cap is not None:
        from omnicrawl.core.config import load_config as load

        config = load(path)
        config.raw["http"]["retry_after_cap_seconds"] = cap
    else:
        config = load_config(path)
    fetcher = HTTPXAsyncFetcher(config)
    fetcher.egress = SimpleNamespace(
        record_failure=lambda *_a, **_k: None,
        record_success=lambda *_a, **_k: None,
        record_response=lambda *_a, **_k: None,
        request=lambda *_a, **_k: contextlib.nullcontext(),
    )
    return fetcher


def test_retry_after_capped_at_60(monkeypatch, tmp_path: Path) -> None:
    from omnicrawl.fetching import async_fetcher as module

    sleeps: list[float] = []

    async def _fake_sleep(wait):
        sleeps.append(wait)

    monkeypatch.setattr(module.asyncio, "sleep", _fake_sleep)
    fetcher = _fetcher(tmp_path)
    with pytest.raises(PermanentFetchError):
        asyncio.run(fetcher._request(_Client("7200"), CrawlRequest("https://example.org/")))
    assert sleeps == [60.0, 60.0]


def test_retry_after_within_cap_untouched(monkeypatch, tmp_path: Path) -> None:
    from omnicrawl.fetching import async_fetcher as module

    sleeps: list[float] = []

    async def _fake_sleep(wait):
        sleeps.append(wait)

    monkeypatch.setattr(module.asyncio, "sleep", _fake_sleep)
    fetcher = _fetcher(tmp_path)
    with pytest.raises(PermanentFetchError):
        asyncio.run(fetcher._request(_Client("30"), CrawlRequest("https://example.org/")))
    assert sleeps == [30.0, 30.0]


def test_retry_after_custom_cap(monkeypatch, tmp_path: Path) -> None:
    from omnicrawl.fetching import async_fetcher as module

    sleeps: list[float] = []

    async def _fake_sleep(wait):
        sleeps.append(wait)

    monkeypatch.setattr(module.asyncio, "sleep", _fake_sleep)
    fetcher = _fetcher(tmp_path, cap=5)
    with pytest.raises(PermanentFetchError):
        asyncio.run(fetcher._request(_Client("7200"), CrawlRequest("https://example.org/")))
    assert sleeps == [5.0, 5.0]


def test_retry_after_parser_handles_http_date() -> None:
    value = retry_after_seconds({"Retry-After": "Wed, 21 Oct 2030 07:28:00 GMT"})
    assert value is not None and value > 0
