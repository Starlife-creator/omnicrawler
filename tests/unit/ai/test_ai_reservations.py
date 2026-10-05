"""Budget reservation must block parallel admission before provider billing."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from omnicrawler.services.ai_safety import AIBudget, AIBudgetExceededError


def test_parallel_reservations_cannot_overbook_tokens():
    budget = AIBudget(maximum_tokens=100)
    def reserve(_):
        try:
            return budget.reserve(tokens=60, cost=0)
        except AIBudgetExceededError:
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        reservations = [r for r in pool.map(reserve, range(8)) if r is not None]
    assert len(reservations) == 1
    budget.settle(reservations[0], tokens=20, cost=0)
    assert budget.tokens == 20
    second = budget.reserve(tokens=60, cost=0)
    budget.settle(second, tokens=None, cost=None)
    assert budget.tokens == 80
    assert budget.unknown_usage_requests == 1


def test_settlement_keeps_real_overrun_and_is_idempotent():
    budget = AIBudget(maximum_tokens=100)
    reservation = budget.reserve(tokens=60, cost=0)
    with pytest.raises(AIBudgetExceededError):
        budget.settle(reservation, tokens=101, cost=0)
    assert budget.tokens == 101
    budget.settle(reservation, tokens=101, cost=0)
    assert budget.tokens == 101
    with pytest.raises(AIBudgetExceededError):
        budget.reserve(tokens=1, cost=0)


def test_missing_price_blocks_finite_cost_budget_before_send():
    from omnicrawler.services.ai_accounting import AIRequestAccounting
    accounting = AIRequestAccounting(AIBudget(maximum_cost=1), {})
    with pytest.raises(AIBudgetExceededError, match="单价"):
        accounting.reserve({"messages": [{"role": "user", "content": "hello"}], "max_tokens": 20})
