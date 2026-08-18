"""S2.5.15：ai_graph 空 choices 防护（降级而非 IndexError）。"""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("aiohttp")

from omnicrawler.extraction.ai_graph import AIGraphExtractor


@pytest.fixture(autouse=True)
def _allow_privacy(monkeypatch):
    """B05-019：本测试聚焦 choices 降级，直接放行外发隐私闸门。"""
    monkeypatch.setattr(
        "omnicrawler.core.ai_env.require_ai_privacy",
        lambda *a, **k: None,
    )


class _Provider:
    base_url = "https://llm.example/"
    timeout_seconds = 10
    api_key = "test"
    model = "test-model"


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


def _run(extractor, response: dict) -> dict:
    async def _fake_post(*_a, **_k):
        return response

    async def _run_impl() -> dict:
        extractor._post_with_retry = _fake_post  # type: ignore[attr-defined]
        return await extractor._extract_chunk("<p>hi</p>", [], 1000, session=_FakeSession())

    return asyncio.run(_run_impl())


def test_empty_choices_degrades_gracefully() -> None:
    extractor = AIGraphExtractor(provider=_Provider())  # type: ignore[arg-type]
    assert _run(extractor, {"choices": []}) == {}


def test_missing_choices_key_degrades() -> None:
    extractor = AIGraphExtractor(provider=_Provider())  # type: ignore[arg-type]
    assert _run(extractor, {"id": "abc"}) == {}


def test_normal_choices_still_parsed() -> None:
    extractor = AIGraphExtractor(provider=_Provider())  # type: ignore[arg-type]
    assert _run(extractor, {"choices": [{"message": {"content": '{"nodes": []}'}}]}) == {
        "nodes": []
    }
