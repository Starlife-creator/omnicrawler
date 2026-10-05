"""Bounded admission control using real responses and local resource pressure."""

from __future__ import annotations

import time
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
        self._samples: deque[tuple[float, bool, bool, float]] = deque(maxlen=16)
        self._healthy = 0
        self._healthy_latency: float | None = None
        self.audit: list[dict[str, Any]] = []

    def observe(self, latency: float, *, failed: bool = False, rate_limited: bool = False,
                resource_pressure: bool = False) -> int:
        if not self.enabled:
            return self.current
        now = time.monotonic()
        if self._samples and now - self._samples[-1][3] > 120:
            self._healthy = 0
            self._healthy_latency = None
        while self._samples and now - self._samples[0][3] > 120:
            self._samples.popleft()
        if not resource_pressure:
            self._samples.append((max(0.0, latency), failed, rate_limited, now))
        if not (failed or rate_limited or resource_pressure):
            self._healthy_latency = (max(0.0, latency) if self._healthy_latency is None
                                     else self._healthy_latency * 0.8 + max(0.0, latency) * 0.2)
        self._healthy = 0 if failed or rate_limited or resource_pressure else self._healthy + 1
        if not (failed or rate_limited or resource_pressure or self._healthy >= 8):
            return self.current
        signals = RuntimeSignals(
            latency_seconds=sum(item[0] for item in self._samples) / max(1, len(self._samples)),
            error_rate=sum(item[1] for item in self._samples) / max(1, len(self._samples)),
            rate_limited=rate_limited or resource_pressure, dom_stability=0.5,
            text_layer_quality=0.5, free_disk_bytes=0,
        )
        for adjustment in self._controller.propose({"concurrency": self.current, "ocr": False,
                                                  "healthy_latency_seconds": self._healthy_latency or 0.8}, signals):
            if adjustment.parameter == "concurrency" and adjustment.before != adjustment.after:
                self.current = int(adjustment.after)
                item = asdict(adjustment)
                if resource_pressure:
                    item["reason"] = "本机 CPU 或内存压力，降低新请求认领量"
                self.audit = (self.audit + [item])[-200:]
        self._healthy = 0
        return self.current


class DomainAdmission:
    """Separate host feedback from global machine pressure and scheduling slots."""

    def __init__(self, maximum: int, *, enabled: bool = True, per_domain: int = 0) -> None:
        self.global_control = LiveConcurrency(maximum, enabled=enabled)
        self.per_domain = max(1, min(maximum, per_domain or maximum))
        self._domains: dict[str, LiveConcurrency] = {}

    @property
    def maximum(self) -> int:
        return self.global_control.maximum

    @property
    def current(self) -> int:
        return self.global_control.current

    @property
    def enabled(self) -> bool:
        return self.global_control.enabled

    def domain_limit(self, scope: str) -> int:
        control = self._domains.get(scope)
        return control.current if control else self.per_domain

    def observe(self, latency: float, *, domain: str = "", failed: bool = False, rate_limited: bool = False,
                resource_pressure: bool = False) -> int:
        if resource_pressure:
            return self.global_control.observe(0, resource_pressure=True)
        if not domain:
            return self.current
        if domain not in self._domains and len(self._domains) >= 1000:
            self._domains.pop(next(iter(self._domains)))
        control = self._domains.setdefault(domain, LiveConcurrency(self.per_domain, enabled=self.enabled))
        control.observe(latency, failed=failed, rate_limited=rate_limited)
        if not failed and not rate_limited:
            self.global_control.observe(latency)
        return self.current

    @property
    def audit(self) -> list[dict[str, Any]]:
        entries = [{**item, "scope": "machine"} for item in self.global_control.audit]
        for scope, control in self._domains.items():
            entries.extend({**item, "scope": scope} for item in control.audit)
        return entries[-200:]


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
