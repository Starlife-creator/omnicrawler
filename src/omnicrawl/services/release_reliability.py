"""Error budgets, canary promotion and automatic rollback decisions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class CanaryObservation:
    channel: Literal["component", "config", "worker", "desktop"]
    version: str
    sample_size: int
    error_rate: float
    crash_rate: float
    recovery_rate: float
    data_loss_events: int = 0


@dataclass(frozen=True, slots=True)
class RolloutDecision:
    action: Literal["hold", "promote", "rollback"]
    reason: str
    observation: CanaryObservation


def decide_rollout(observation: CanaryObservation, *, error_budget: float = 0.02) -> RolloutDecision:
    if observation.data_loss_events or observation.crash_rate > error_budget or observation.error_rate > error_budget * 2:
        return RolloutDecision("rollback", "超过错误预算或检测到数据损失", observation)
    if observation.sample_size < 20:
        return RolloutDecision("hold", "金丝雀样本不足", observation)
    if observation.recovery_rate < 0.8:
        return RolloutDecision("hold", "中断恢复率尚未达到SLO", observation)
    return RolloutDecision("promote", "金丝雀指标满足推广门禁", observation)


def incident_timeline(events: list[dict[str, object]]) -> list[dict[str, object]]:
    return sorted(events, key=lambda event: str(event.get("timestamp", "")))
