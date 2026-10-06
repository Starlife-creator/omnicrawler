import asyncio
from contextlib import nullcontext
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest

from omnicrawler.core.config import AppConfig
from omnicrawler.core.errors import TaskStoppedError
from omnicrawler.services.ai_accounting import AIRequestAccounting
from omnicrawler.services.ai_providers import OpenAICompatibleProvider
from omnicrawler.services.ai_safety import AIBudget, AIBudgetExceededError


def provider(event=None):
    value = OpenAICompatibleProvider("test", {"base_url": "https://example.com/v1", "model": "test",
                                             "timeout_seconds": 5}, cancel_event=event)
    value.app_config = AppConfig(Path("test.yaml"), Path.cwd(), {"http": {}}, Path.cwd(), ())
    value.egress = type("Broker", (), {"policy": None, "request": lambda *a, **k: nullcontext()})()
    return value


def test_cancelled_request_does_not_send_or_charge_attempt():
    event = Event()
    event.set()
    value = provider(event)
    with patch("omnicrawler.services.ai_providers.build_safe_opener") as opener:
        with pytest.raises(TaskStoppedError):
            value.generate([{"role": "user", "content": "hello"}])
    opener.assert_not_called()
    assert value.budget.requests == 0


def test_cancel_during_failure_prevents_retry():
    event = Event()
    value = provider(event)

    def fail(*args, **kwargs):
        event.set()
        raise TimeoutError("timeout")

    with patch("omnicrawler.services.ai_providers.build_safe_opener") as factory:
        factory.return_value.open.side_effect = fail
        with pytest.raises(TaskStoppedError):
            value.generate([{"role": "user", "content": "hello"}])
    assert factory.return_value.open.call_count == 1
    assert value.budget.unknown_usage_requests == 1


def test_unknown_usage_stops_further_finite_token_budget_requests():
    value = AIRequestAccounting(AIBudget(maximum_tokens=10000), {})
    payload = {"messages": [], "max_tokens": 10}
    reservation = value.reserve(payload)
    value.settle(reservation, None)
    with pytest.raises(AIBudgetExceededError, match="未知"):
        value.reserve(payload)


def test_operation_budget_replacement_updates_admission_and_retry_accounting(monkeypatch):
    value = provider()
    operation_budget = AIBudget(maximum_requests=1)
    value.budget = operation_budget
    monkeypatch.setattr(value, "_wait_retry", lambda *_args: None)
    with patch("omnicrawler.services.ai_providers.build_safe_opener") as factory:
        factory.return_value.open.side_effect = TimeoutError("retryable")
        with pytest.raises(AIBudgetExceededError):
            value.generate([{"role": "user", "content": "bounded operation"}])
    assert factory.return_value.open.call_count == 1
    assert value.accounting.budget is operation_budget
    assert operation_budget.requests == 1 and operation_budget.logical_requests == 1
    assert operation_budget.unknown_usage_requests == 1


def test_spent_budget_cannot_be_reset_by_replacement():
    value = provider()
    value.budget.reserve(tokens=1, cost=0)
    with pytest.raises(ValueError, match="不能替换预算"):
        value.budget = AIBudget()


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_timeout_must_be_finite_positive(timeout):
    with pytest.raises(ValueError):
        OpenAICompatibleProvider("test", {"base_url": "https://example.com/v1", "model": "test",
                                           "timeout_seconds": timeout})


def test_elapsed_deadline_rejects_response_and_stops_retries(monkeypatch):
    value = provider()
    value.total_timeout = 1
    clock = [0.0]
    monkeypatch.setattr("omnicrawler.services.ai_providers.time.monotonic", lambda: clock[0])

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, maximum):
            clock[0] = 2
            return b'{"choices": [{"message": {"content": "ok"}}]}'

        def geturl(self):
            return "https://example.com/v1"

    with patch("omnicrawler.services.ai_providers.build_safe_opener") as factory:
        factory.return_value.open.return_value = Response()
        with pytest.raises(RuntimeError, match="截止"):
            value.generate([{"role": "user", "content": "hello"}])
    assert factory.return_value.open.call_count == 1
    assert value.budget.unknown_usage_requests == 1


@pytest.mark.asyncio
async def test_async_deadline_cancels_response_and_releases_reservation():
    from omnicrawler.extraction.ai_graph import AIGraphExtractor, Provider

    closed = []
    budget = AIBudget()
    broker = type("Broker", (), {"request": lambda *a, **k: nullcontext()})()
    extractor = AIGraphExtractor(provider=Provider(total_timeout_seconds=0.02), egress=broker, budget=budget)

    class Response:
        status = 200

        def __init__(self):
            self.content = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closed.append(True)

        async def read(self, maximum):
            await asyncio.Event().wait()

    session = type("Session", (), {"post": lambda *a, **k: Response()})()
    with pytest.raises(TimeoutError):
        await extractor._post_with_retry(session, "https://example.com", payload={}, headers={}, timeout=None)
    assert closed == [True]
    assert budget.requests == 1
    assert not budget._reservations
    assert budget.unknown_usage_requests == 1


@pytest.mark.asyncio
async def test_cancelled_chunk_cannot_be_reported_as_success():
    from unittest.mock import AsyncMock

    from omnicrawler.extraction.ai_graph import AIGraphExtractor, FieldDef

    extractor = AIGraphExtractor()
    with patch.object(extractor, "_extract_chunk", new=AsyncMock(side_effect=asyncio.CancelledError())):
        with pytest.raises(asyncio.CancelledError):
            await extractor.extract("hello", [FieldDef("title")])
