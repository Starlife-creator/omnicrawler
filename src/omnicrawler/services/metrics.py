from __future__ import annotations

import json
import os
import shutil
import threading
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..core.models import FetchResult
from ..core.site_aliases import SiteAliasRegistry
from ..core.structure_fingerprint import StructureFingerprintRegistry, structure_fingerprint
from ..core.utils import atomic_write, utcnow

MAX_TIMING_SAMPLES = 10_000


class RunMetrics:
    """Dependency-free runtime metrics with Prometheus and JSON snapshots."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[tuple[str, tuple[tuple[str, str], ...]]] = Counter()
        # Retaining every latency can turn observability itself into a memory
        # problem on long runs.  A bounded recent sample preserves useful p50/p95
        # measurements while totals remain exact.
        self._latencies: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=MAX_TIMING_SAMPLES)
        )
        self._latency_counts: Counter[str] = Counter()
        self._latency_totals: dict[str, float] = defaultdict(float)
        self._latency_maximums: dict[str, float] = defaultdict(float)
        self._stage_latencies: dict[str, deque[float]] = defaultdict(
            lambda: deque(maxlen=MAX_TIMING_SAMPLES)
        )
        self._stage_counts: Counter[str] = Counter()
        self._stage_totals: dict[str, float] = defaultdict(float)
        self._stage_maximums: dict[str, float] = defaultdict(float)
        self._gauges: dict[str, float] = {}
        # P2-1：结构指纹注册表 — 跟踪本 run 内出现的记录结构模板
        self._structure_registry = StructureFingerprintRegistry()

    def increment(self, name: str, value: int = 1, **labels: Any) -> None:
        normalized = tuple(sorted((str(key), str(item)) for key, item in labels.items()))
        with self._lock:
            self._counters[(name, normalized)] += value

    def gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = float(value)

    def record_fetch(self, result: FetchResult, *, engine: str, escalated: bool = False) -> None:
        raw_host = (urlsplit(result.final_url).hostname or "unknown").casefold()
        # P2-5：指标侧把多域名 / 多环境镜像归并为 canonical（例如 m.→主站、preview→prod）。
        # 失败回退到 raw_host，绝对不让观测层影响抓取主流程。
        try:
            host = SiteAliasRegistry.default().resolve(raw_host) or raw_host
        except Exception:  # noqa: BLE001 — 别名解析异常绝不影响主流程
            host = raw_host
        self.increment("omnicrawler_requests_total", host=host, engine=engine, status=result.status)
        self.increment("omnicrawler_response_bytes_total", len(result.body), host=host)
        if escalated:
            self.increment("omnicrawler_browser_escalations_total", host=host)
        with self._lock:
            self._record_timing(
                self._latencies,
                self._latency_counts,
                self._latency_totals,
                self._latency_maximums,
                host,
                float(result.elapsed_seconds),
            )

    def record_extracted(self, record: Any) -> None:
        """P2-1：记录一条 ExtractedRecord 的结构指纹。

        仅取 record.data 的键集合 + 类型签名做结构指纹，
        不含值内容；用于"本 run 内有几种结构模板"聚合和结构漂移检测。
        异常绝不影响主流程。
        """
        try:
            data = record.data
            rtype = getattr(record, "record_type", "")
            source_url = getattr(record, "source_url", "")
            sig = structure_fingerprint(data, record_type=rtype)
            is_drift = self._structure_registry.observe(sig, source_url)
            self.increment("omnicrawler_structure_templates_total", template=sig)
            if is_drift:
                self.increment("omnicrawler_structure_drift_total", url=source_url)
        except Exception:  # noqa: BLE001 — 观测层异常不得影响提取主流程
            pass

    def record_stage(self, stage: str, seconds: float) -> None:
        """Record a completed pipeline stage without retaining unbounded samples."""
        if seconds < 0:
            raise ValueError("stage duration cannot be negative")
        with self._lock:
            self._record_timing(
                self._stage_latencies,
                self._stage_counts,
                self._stage_totals,
                self._stage_maximums,
                stage,
                float(seconds),
            )

    @staticmethod
    def _record_timing(
        samples: dict[str, deque[float]],
        counts: Counter[str],
        totals: dict[str, float],
        maximums: dict[str, float],
        key: str,
        seconds: float,
    ) -> None:
        samples[key].append(seconds)
        counts[key] += 1
        totals[key] += seconds
        maximums[key] = max(maximums[key], seconds)

    def snapshot(self, workspace: Path | None = None) -> dict[str, Any]:
        with self._lock:
            counters = [
                {"name": name, "labels": dict(labels), "value": value}
                for (name, labels), value in sorted(self._counters.items())
            ]
            latency = self._timing_snapshot(
                self._latencies, self._latency_counts, self._latency_totals, self._latency_maximums
            )
            stages = self._timing_snapshot(
                self._stage_latencies, self._stage_counts, self._stage_totals, self._stage_maximums
            )
            gauges = dict(self._gauges)
        system: dict[str, Any] = {"cpu_count": os.cpu_count()}
        if workspace is not None:
            usage = shutil.disk_usage(workspace)
            system["disk"] = {"total": usage.total, "used": usage.used, "free": usage.free}
        try:
            import psutil
        except ImportError:
            pass
        else:
            memory = psutil.virtual_memory()
            system.update({
                "cpu_percent": psutil.cpu_percent(interval=None),
                "memory": {"total": memory.total, "used": memory.used, "available": memory.available},
                "network": dict(psutil.net_io_counters()._asdict()),
            })
        return {
            "captured_at": utcnow(),
            "counters": counters,
            "latency_by_host": latency,
            "stage_durations": stages,
            "gauges": gauges,
            "system": system,
            # P2-1：结构模板摘要（unique 模板数 + top 签名）
            "structure_templates": {
                "unique": self._structure_registry.unique_count(),
                "top": self._structure_registry.top_signatures(5),
            },
        }

    @staticmethod
    def _timing_snapshot(
        samples: dict[str, deque[float]],
        counts: Counter[str],
        totals: dict[str, float],
        maximums: dict[str, float],
    ) -> dict[str, dict[str, float | int]]:
        snapshot: dict[str, dict[str, float | int]] = {}
        for key, values in samples.items():
            if not values:
                continue
            ordered = sorted(values)
            count = counts[key]
            snapshot[key] = {
                "count": count,
                "sample_count": len(ordered),
                "average_seconds": totals[key] / count,
                "maximum_seconds": maximums[key],
                "p50_seconds": _percentile(ordered, 0.50),
                "p95_seconds": _percentile(ordered, 0.95),
            }
        return snapshot

    def prometheus(self) -> str:
        lines: list[str] = []
        with self._lock:
            counters = list(self._counters.items())
            latencies = self._timing_snapshot(
                self._latencies, self._latency_counts, self._latency_totals, self._latency_maximums
            )
            stages = self._timing_snapshot(
                self._stage_latencies, self._stage_counts, self._stage_totals, self._stage_maximums
            )
            gauges = dict(self._gauges)
        for (name, labels), counter_value in sorted(counters):
            lines.append(f"{_metric(name)}{_labels(labels)} {counter_value}")
        for name, gauge_value in sorted(gauges.items()):
            lines.append(f"{_metric(name)} {gauge_value}")
        for host, values in sorted(latencies.items()):
            labels = (("host", host),)
            lines.append(f"omnicrawler_request_duration_seconds_count{_labels(labels)} {values['count']}")
            lines.append(
                f"omnicrawler_request_duration_seconds_sum{_labels(labels)} "
                f"{values['average_seconds'] * values['count']:.6f}"
            )
            for percentile, metric_suffix in (
                ("p50_seconds", "p50"),
                ("p95_seconds", "p95"),
                # Keep the pre-existing Prometheus ``_max`` name stable.
                ("maximum_seconds", "max"),
            ):
                lines.append(
                    f"omnicrawler_request_duration_seconds_{metric_suffix}"
                    f"{_labels(labels)} {values[percentile]:.6f}"
                )
        for stage, values in sorted(stages.items()):
            labels = (("stage", stage),)
            lines.append(f"omnicrawler_stage_duration_seconds_count{_labels(labels)} {values['count']}")
            lines.append(
                f"omnicrawler_stage_duration_seconds_sum{_labels(labels)} "
                f"{values['average_seconds'] * values['count']:.6f}"
            )
            lines.append(f"omnicrawler_stage_duration_seconds_p50{_labels(labels)} {values['p50_seconds']:.6f}")
            lines.append(f"omnicrawler_stage_duration_seconds_p95{_labels(labels)} {values['p95_seconds']:.6f}")
            lines.append(f"omnicrawler_stage_duration_seconds_max{_labels(labels)} {values['maximum_seconds']:.6f}")
        return "\n".join(lines) + ("\n" if lines else "")

    def write(self, output: Path, workspace: Path | None = None) -> dict[str, str]:
        output.mkdir(parents=True, exist_ok=True)
        json_path = output / "metrics.json"
        prometheus_path = output / "metrics.prom"
        atomic_write(json_path, json.dumps(self.snapshot(workspace), ensure_ascii=False, indent=2).encode("utf-8"))
        atomic_write(prometheus_path, self.prometheus().encode("utf-8"))
        return {"json": str(json_path), "prometheus": str(prometheus_path)}


def _metric(name: str) -> str:
    return "".join(char if char.isalnum() or char in "_:" else "_" for char in name)


def _labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    escaped = [f'{_metric(key)}="{value.replace(chr(92), chr(92) * 2).replace(chr(34), chr(92) + chr(34))}"' for key, value in labels]
    return "{" + ",".join(escaped) + "}"


def _percentile(values: list[float], quantile: float) -> float:
    """Return a deterministic linear-interpolated percentile for a sorted sample."""
    if not values:
        raise ValueError("cannot calculate a percentile of an empty sample")
    position = (len(values) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)
