"""AI 预算计量测试（Phase 1e blocking 回归：requests 单计、token 超限熔断）。"""

from __future__ import annotations

import pytest

from omnicrawl.services.ai_providers import OpenAICompatibleProvider
from omnicrawl.services.ai_safety import AIBudget, AIBudgetExceededError


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


def _provider(budget: AIBudget) -> OpenAICompatibleProvider:
    from pathlib import Path

    from omnicrawl.core.config import AppConfig

    config = {
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-x",
        "model": "gpt-x",
        "timeout_seconds": 30,
    }
    provider = OpenAICompatibleProvider("default", config, budget=budget)
    raw = {
        "project": {"name": "test"},
        "source": {"kind": "crawl"},
        "http": {"max_response_bytes": 1_000_000},
    }
    provider.app_config = AppConfig(Path("x.yaml"), Path.cwd(), raw, Path.cwd(), ())
    provider.egress = _FakeEgress()  # type: ignore[assignment]
    return provider


def _patch_opener(provider: OpenAICompatibleProvider) -> None:
    """替换 generate 内的 build_safe_opener 调用，避免真实网络与 DNS。"""
    from unittest.mock import patch

    class _FakeOpener:
        def open(self, request, timeout=None):
            payload = '{"choices":[{"message":{"content":"ok"}}],"usage":{"total_tokens": 100}}'
            return _FakeResponse(payload.encode("utf-8"))

    return patch(
        "omnicrawl.services.ai_providers.build_safe_opener",
        return_value=_FakeOpener(),
    )


def test_request_count_single_increment_per_call() -> None:
    """maximum_requests=3 时应允许恰好 3 次调用，第 4 次被拒（修复双计）。"""
    budget = AIBudget(maximum_requests=3)
    provider = _provider(budget)
    with _patch_opener(provider):
        for _ in range(3):
            provider.generate([{"role": "user", "content": "hi"}])
    assert budget.requests == 3
    with pytest.raises(AIBudgetExceededError, match="请求预算"):
        provider.generate([{"role": "user", "content": "hi"}])


def test_token_budget_trips_and_stays_tripped() -> None:
    """maximum_tokens=250：两次调用（各 100）后第三次响应结算超限，
    且超限后预占检查也应拒发（熔断），而非无限发出。"""
    budget = AIBudget(maximum_tokens=250)
    provider = _provider(budget)
    with _patch_opener(provider):
        provider.generate([{"role": "user", "content": "hi"}])  # tokens=100
        provider.generate([{"role": "user", "content": "hi"}])  # tokens=200
        with pytest.raises(AIBudgetExceededError, match="Token 预算"):
            provider.generate([{"role": "user", "content": "hi"}])  # 结算 300 > 250
        # 熔断：下一次请求在预占阶段即被拒（请求未发出）
        with pytest.raises(AIBudgetExceededError, match="Token 预算"):
            provider.generate([{"role": "user", "content": "hi"}])


def test_cost_budget_trips_and_stays_tripped() -> None:
    """maximum_cost 按估算单价结算：超限抛可区分异常且后续预占拒发。"""
    # 0.000002 * 100 tokens = 0.0002/次；上限 0.0003 → 第 2 次结算即超
    budget = AIBudget(maximum_cost=0.0003)
    provider = _provider(budget)
    with _patch_opener(provider):
        provider.generate([{"role": "user", "content": "hi"}])  # cost=0.0002
        with pytest.raises(AIBudgetExceededError, match="费用预算"):
            provider.generate([{"role": "user", "content": "hi"}])  # 结算 0.0004 > 0.0003
        with pytest.raises(AIBudgetExceededError, match="费用预算"):
            provider.generate([{"role": "user", "content": "hi"}])  # 预占熔断


def test_budget_exceeded_is_distinct_error() -> None:
    assert AIBudgetExceededError is not RuntimeError
    assert issubclass(AIBudgetExceededError, RuntimeError)
