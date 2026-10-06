import pytest

from omnicrawler.services.ai_accounting import AIRequestAccounting
from omnicrawler.services.ai_safety import AIBudget, AIBudgetExceededError


def accounting(prices=None):
    return AIRequestAccounting(AIBudget(maximum_cost=1), {
        "input_per_million": 2, "output_per_million": 4, **(prices or {}),
    })


def settle(value, details):
    reservation = value.reserve({"messages": [], "max_tokens": 10})
    return value.settle(reservation, {"usage": {
        "prompt_tokens": 100, "completion_tokens": 10,
        "prompt_tokens_details": details,
    }})


def test_cached_read_and_write_are_disjoint_input_classes():
    value = accounting({"cached_input_per_million": 0.5, "cache_write_per_million": 3})
    result = settle(value, {"cached_tokens": 60, "cache_write_tokens": 20})
    assert result["estimated_cost"] == pytest.approx((20 * 2 + 60 * 0.5 + 20 * 3 + 10 * 4) / 1_000_000)
    assert value.budget.tokens == 110
    assert result["cached_input_tokens"] == 60
    assert result["cache_write_tokens"] == 20
    assert result["pricing_source"] == "local_configuration"
    assert result["billing_verified"] is False


def test_missing_cache_price_is_unknown_and_stops_finite_cost_budget():
    value = accounting()
    result = settle(value, {"cached_tokens": 60})
    assert not result["cost_known"]
    assert result["estimated_cost"] is None
    with pytest.raises(AIBudgetExceededError):
        value.reserve({"messages": [], "max_tokens": 10})


@pytest.mark.parametrize("details", [
    {"cached_tokens": True}, {"cached_tokens": -1}, {"cached_tokens": 110},
    {"cached_tokens": 60, "cache_write_tokens": 60}, {"audio_tokens": 1}, "invalid",
])
def test_invalid_or_unsupported_breakdown_cannot_claim_known_cost(details):
    assert not settle(accounting({"cached_input_per_million": 0.5}), details)["cost_known"]


def test_invalid_cached_price_fails_before_request():
    with pytest.raises(ValueError):
        accounting({"cached_input_per_million": float("nan")})
