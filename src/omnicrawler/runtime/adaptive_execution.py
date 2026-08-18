"""Explainable bounded execution tuning that cannot expand authorization scope."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class RuntimeSignals:
    latency_seconds: float
    error_rate: float
    rate_limited: bool
    dom_stability: float
    text_layer_quality: float
    free_disk_bytes: int


@dataclass(frozen=True, slots=True)
class Adjustment:
    parameter: str
    before: Any
    after: Any
    reason: str
    lower_bound: Any
    upper_bound: Any


class AdaptiveController:
    def __init__(self, *, enabled: bool = True, minimum_concurrency: int = 1, maximum_concurrency: int = 8, minimum_free_disk: int = 536_870_912) -> None:
        self.enabled = enabled
        self.minimum_concurrency = minimum_concurrency
        self.maximum_concurrency = maximum_concurrency
        self.minimum_free_disk = minimum_free_disk
        self.audit: list[Adjustment] = []

    def propose(self, current: dict[str, Any], signals: RuntimeSignals) -> tuple[Adjustment, ...]:
        if not self.enabled:
            return ()
        result: list[Adjustment] = []
        concurrency = max(self.minimum_concurrency, min(self.maximum_concurrency, int(current.get("concurrency", 2))))
        if signals.rate_limited or signals.error_rate > 0.2:
            result.append(Adjustment("concurrency", concurrency, max(self.minimum_concurrency, concurrency - 1), "限流或错误率升高", self.minimum_concurrency, self.maximum_concurrency))
        elif signals.latency_seconds < 1 and signals.error_rate < 0.02:
            result.append(Adjustment("concurrency", concurrency, min(self.maximum_concurrency, concurrency + 1), "响应稳定且延迟较低", self.minimum_concurrency, self.maximum_concurrency))
        wait = float(current.get("wait_seconds", 1))
        if signals.dom_stability > 0.95:
            result.append(Adjustment("wait_seconds", wait, max(0.2, wait * 0.75), "DOM连续样本稳定", 0.2, 30.0))
        elif signals.dom_stability < 0.5:
            result.append(Adjustment("wait_seconds", wait, min(30.0, wait * 1.5), "动态页面尚未稳定", 0.2, 30.0))
        ocr = bool(current.get("ocr", True))
        if ocr and signals.text_layer_quality >= 0.95:
            result.append(Adjustment("ocr", True, False, "PDF文本层质量充分", False, True))
        if signals.free_disk_bytes < self.minimum_free_disk:
            result.append(Adjustment("run_state", "running", "pause", "磁盘低于安全阈值；不会自动删除证据", "pause", "running"))
        # S2.5.27：audit 有界，不无限增长
        self.audit = (self.audit + result)[-500:]
        return tuple(result)

    def audit_mapping(self) -> list[dict[str, Any]]:
        return [asdict(item) for item in self.audit]


def attachment_duplicate(url: str, headers: dict[str, str], sha256: str, known: set[tuple[str, str, str]]) -> bool:
    identity = (url, headers.get("etag", headers.get("last-modified", "")), sha256)
    return identity in known

