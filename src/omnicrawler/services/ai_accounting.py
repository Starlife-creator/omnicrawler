"""Shared accounting for synchronous and asynchronous OpenAI-compatible requests."""
from __future__ import annotations

import json
import math
from typing import Any

from .ai_safety import AIBudget, AIBudgetExceededError


class AIRequestAccounting:
    def __init__(self, budget: AIBudget, pricing: dict[str, Any]) -> None:
        self.budget = budget
        self.pricing = pricing
        for key in ("input_per_million", "output_per_million"):
            value = pricing.get(key)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError(f"AI pricing.{key} 必须为有限非负数字")

    @property
    def priced(self) -> bool:
        return all(type(self.pricing.get(key)) in (int, float) for key in ("input_per_million", "output_per_million"))

    def reserve(self, payload: dict[str, Any]) -> str:
        output = payload.get("max_tokens")
        bounded = self.budget.maximum_tokens or self.budget.maximum_cost
        if bounded and (type(output) is not int or output <= 0):
            raise AIBudgetExceededError("有限 AI 预算需要设置单次 max_tokens 上限")
        if self.budget.maximum_cost and not self.priced:
            raise AIBudgetExceededError("AI 费用预算需要配置该模型的输入和输出单价")
        # UTF-8 bytes plus protocol margin is a conservative local estimate,
        # not a tokenizer measurement or a guarantee about provider billing.
        incoming = len(json.dumps(payload.get("messages", []), ensure_ascii=False).encode("utf-8")) + 256
        outgoing = output if type(output) is int and output > 0 else 0
        reserved_tokens = incoming + outgoing if bounded else 0
        cost = self._cost(incoming, outgoing) if self.priced else 0.0
        return self.budget.reserve(tokens=reserved_tokens, cost=cost or 0.0)

    def _cost(self, incoming: int, outgoing: int) -> float | None:
        if not self.priced:
            return None
        return (incoming * self.pricing["input_per_million"] + outgoing * self.pricing["output_per_million"]) / 1_000_000

    def settle(self, identity: str, value: dict[str, Any] | None) -> dict[str, Any]:
        usage = value.get("usage", {}) if isinstance(value, dict) else {}
        usage = usage if isinstance(usage, dict) else {}
        def count(key: str) -> int | None:
            item = usage.get(key)
            return item if type(item) is int and item >= 0 else None
        incoming, outgoing = count("prompt_tokens"), count("completion_tokens")
        tokens = count("total_tokens")
        if incoming is not None and outgoing is not None:
            tokens = max(tokens or 0, incoming + outgoing)
        cost = self._cost(incoming, outgoing) if incoming is not None and outgoing is not None else None
        self.budget.settle(identity, tokens=tokens, cost=cost)
        return {"usage_known": tokens is not None, "cost_known": cost is not None,
                "estimated_cost": cost, "currency": self.pricing.get("currency", "unspecified"),
                "reservation_estimate": "utf8_bytes_plus_margin",
                "logical_requests": self.budget.logical_requests, "network_attempts": self.budget.requests,
                "unknown_usage_attempts": self.budget.unknown_usage_requests,
                "unknown_cost_attempts": self.budget.unknown_cost_requests}
