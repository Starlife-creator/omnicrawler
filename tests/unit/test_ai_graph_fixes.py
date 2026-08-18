"""ai_graph 健壮化修复测试（Phase 1f：D54/D55/D56/D58/D60 + C34 注入防护）。"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("aiohttp")

from omnicrawler.extraction.ai_graph import AIGraphExtractor, FieldDef, Provider, SplitStrategy


class _FakeResp:
    def __init__(self, status: int, body: str) -> None:
        self.status = status
        self._body = body

    async def text(self) -> str:
        return self._body

    async def json(self) -> dict:
        return json.loads(self._body)


class _FakeCM:
    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp

    async def __aenter__(self) -> _FakeResp:
        return self._resp

    async def __aexit__(self, *args) -> bool:
        return False


class _FakeSession:
    def __init__(self, *responses: _FakeResp) -> None:
        self._responses = list(responses)
        self.post_calls: list[tuple[str, dict]] = []

    def post(self, url: str, **kwargs) -> _FakeCM:
        self.post_calls.append((url, kwargs))
        return _FakeCM(self._responses.pop(0))


@pytest.mark.asyncio
async def test_d58_heading_split_keeps_prefix() -> None:
    ex = AIGraphExtractor()
    html = "<div>发布时间：2026-01-01</div><h1>标题</h1>正文"
    chunks = ex._heading_split(html)
    assert len(chunks) == 2
    assert "发布时间" in chunks[0]
    assert "标题" in chunks[1]


@pytest.mark.asyncio
async def test_d55_all_chunks_failed_raises() -> None:
    ex = AIGraphExtractor()

    async def fail(*args, **kwargs):
        raise RuntimeError("boom")

    ex._extract_chunk = fail  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="全部分块失败"):
        await ex.extract("<p>content</p>", [FieldDef(name="title")], SplitStrategy.FIXED_CHUNK)


@pytest.mark.asyncio
async def test_d54_http_error_raises_with_status() -> None:
    ex = AIGraphExtractor()
    session = _FakeSession(_FakeResp(401, '{"error":"bad key"}'))
    with pytest.raises(RuntimeError, match="HTTP 401"):
        await ex._post_with_retry(
            session, "https://api.example.com/v1/chat/completions",
            payload={"model": "m"}, headers={}, timeout=None,
        )
    assert len(session.post_calls) == 1  # 401 不重试


@pytest.mark.asyncio
async def test_d60_retry_on_429_then_success() -> None:
    ex = AIGraphExtractor(max_retries=3)
    session = _FakeSession(
        _FakeResp(429, "rate limited"),
        _FakeResp(200, '{"choices":[{"message":{"content":"{}"}}]}'),
    )
    result = await ex._post_with_retry(
        session, "https://api.example.com/v1/chat/completions",
        payload={"model": "m"}, headers={}, timeout=None,
    )
    assert result["choices"]
    assert len(session.post_calls) == 2  # 429 后重试成功


@pytest.mark.asyncio
async def test_c34_html_marked_untrusted_in_prompt(monkeypatch) -> None:
    # B05-019：外发隐私闸门默认 fail-closed，测试显式开启 allow_page_text
    monkeypatch.setattr(
        "omnicrawler.core.ai_env.require_ai_privacy",
        lambda *a, **k: None,
    )
    ex = AIGraphExtractor(provider=Provider(api_key="sk-test"))
    session = _FakeSession(_FakeResp(200, '{"choices":[{"message":{"content":"{}"}}]}'))
    await ex._extract_chunk("<script>alert(1)</script>", [FieldDef(name="t")], 1000, session=session)
    assert len(session.post_calls) == 1
    payload = session.post_calls[0][1]["json"]
    user_content = payload["messages"][1]["content"]
    assert "UNTRUSTED_EXTERNAL_CONTENT" in user_content
    # 分块阶段已控制长度：原文不被二次截断
    assert "<script>alert(1)</script>" in user_content


# ── P9-A2（B13-002）：发送前强制过 EgressBroker 出口策略 ─────────────


def _egress_broker(tmp_path):
    """默认策略（禁私网）的 EgressBroker，供 ai_graph 注入。"""

    from omnicrawler.core.config import DEFAULTS, AppConfig
    from omnicrawler.security.egress import EgressBroker

    raw = dict(DEFAULTS)
    raw["http"] = dict(raw.get("http", {}))
    raw["http"]["allow_private_network"] = False
    cfg = AppConfig(tmp_path / "task.yaml", tmp_path, raw, tmp_path / "work")
    return EgressBroker(cfg)


async def _post_impl(extractor, url: str) -> dict:
    from omnicrawler.security.policy import PolicyBlockedError

    with pytest.raises(PolicyBlockedError):
        await extractor._post_with_retry(
            _FakeSession(_FakeResp(200, '{"choices":[]}')),
            url,
            payload={"model": "m"},
            headers={},
            timeout=None,
        )
    return {"blocked": True}


def test_ai_graph_egress_blocks_private_target(tmp_path) -> None:
    """注入 egress 后私网元数据目标在发送前即被拦截（不触网）。"""
    from omnicrawler.security.policy import PolicyBlockedError

    extractor = AIGraphExtractor(provider=Provider(api_key="sk-test"), egress=_egress_broker(tmp_path))
    try:
        import asyncio

        asyncio.run(_post_impl(extractor, "http://169.254.169.254/latest/meta-data"))
    except PolicyBlockedError:
        raise AssertionError("应已在 _post_impl 内捕获")


def test_ai_graph_without_egress_does_not_policy_check(tmp_path) -> None:
    """未注入 egress 时保持原有行为（实验性组件可选集成）。"""
    import asyncio

    extractor = AIGraphExtractor(provider=Provider(api_key="sk-test"))

    async def _run() -> dict:
        return await extractor._post_with_retry(
            _FakeSession(_FakeResp(200, '{"choices":[]}')),
            "https://api.example.com/v1/chat/completions",
            payload={"model": "m"},
            headers={},
            timeout=None,
        )

    assert asyncio.run(_run())["choices"] == []
