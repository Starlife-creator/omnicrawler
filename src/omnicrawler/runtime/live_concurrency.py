"""Bounded admission control using real responses and local resource pressure."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict
from typing import Any

from .adaptive_execution import AdaptiveController, RuntimeSignals


class LiveConcurrency:
    def __init__(self, maximum: int, *, enabled: bool = True) -> None:
        self.maximum = max(1, maximum)
        self.current = self.maximum
        self.enabled = enabled
        self._controller = AdaptiveController(maximum_concurrency=self.maximum, minimum_free_disk=0)
        self._samples: deque[tuple[float, bool, bool]] = deque(maxlen=16)
        self._healthy = 0
        self.audit: list[dict[str, Any]] = []

    def observe(self, latency: float, *, failed: bool = False, rate_limited: bool = False,
                resource_pressure: bool = False) -> int:
        if not self.enabled:
            return self.current
        self._samples.append((max(0.0, latency), failed, rate_limited))
        self._healthy = 0 if failed or rate_limited or resource_pressure else self._healthy + 1
        if not (failed or rate_limited or resource_pressure or self._healthy >= 8):
            return self.current
        signals = RuntimeSignals(
            latency_seconds=sum(item[0] for item in self._samples) / len(self._samples),
            error_rate=sum(item[1] for item in self._samples) / len(self._samples),
            rate_limited=rate_limited or resource_pressure, dom_stability=0.5,
            text_layer_quality=0.5, free_disk_bytes=0,
        )
        for adjustment in self._controller.propose({"concurrency": self.current, "ocr": False}, signals):
            if adjustment.parameter == "concurrency" and adjustment.before != adjustment.after:
                self.current = int(adjustment.after)
                item = asdict(adjustment)
                if resource_pressure:
                    item["reason"] = "本机 CPU 或内存压力，降低新请求认领量"
                self.audit = (self.audit + [item])[-200:]
        self._healthy = 0
        return self.current


def local_resource_pressure(maximum_memory_bytes: int = 0) -> bool:
    """Optional psutil probe; unavailable telemetry never means zero usage."""
    try:
        import psutil
    except ImportError:
        return False
    try:
        memory = psutil.virtual_memory()
        if memory.percent >= 90 or psutil.cpu_percent(interval=None) >= 95:
            return True
        if maximum_memory_bytes:
            owner = psutil.Process()
            total = owner.memory_info().rss
            for child in owner.children(recursive=True):
                try:
                    total += child.memory_info().rss
                except psutil.NoSuchProcess:
                    continue
            return total >= maximum_memory_bytes * 0.85
    except (OSError, psutil.Error):
        return False
    return False
