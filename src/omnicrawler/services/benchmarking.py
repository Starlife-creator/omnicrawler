"""Repeatable benchmark measurements and historical comparison.

Provides benchmark profiles, a runner that measures pipeline throughput,
and historical baseline comparison for performance regression detection.
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    """Single benchmark run measurement.

    Attributes:
        profile: Profile name (``"low"``, ``"standard"``, ``"high"``).
        pages: Number of pages processed.
        duration_seconds: Wall-clock duration.
        peak_memory_bytes: Peak process memory (RSS) in bytes.
        bytes_transferred: Total network bytes received.
        errors: Number of errors encountered.
    """

    profile: str
    pages: int
    duration_seconds: float
    peak_memory_bytes: int
    bytes_transferred: int
    errors: int

    @property
    def pages_per_second(self) -> float:
        return self.pages / self.duration_seconds if self.duration_seconds else 0.0

    @property
    def seconds_per_thousand_pages(self) -> float:
        return self.duration_seconds * 1000 / self.pages if self.pages else 0.0

    def to_mapping(self) -> dict[str, object]:
        return {**asdict(self), "pages_per_second": self.pages_per_second, "seconds_per_thousand_pages": self.seconds_per_thousand_pages}


def summarize_benchmarks(results: Iterable[BenchmarkResult]) -> dict[str, object]:
    """Aggregate multiple benchmark results into a summary dict."""
    values = list(results)
    if not values:
        return {"runs": 0}
    return {
        "runs": len(values), "profiles": sorted({item.profile for item in values}),
        "median_pages_per_second": statistics.median(item.pages_per_second for item in values),
        "peak_memory_bytes": max(item.peak_memory_bytes for item in values),
        "total_errors": sum(item.errors for item in values),
    }


def compare_benchmark(before: BenchmarkResult, after: BenchmarkResult, *, regression_threshold: float = 0.1) -> dict[str, object]:
    """Compare two benchmark results and detect regressions.

    Args:
        before: Baseline result.
        after: Current result to compare against baseline.
        regression_threshold: Fractional throughput drop that triggers
            a regression flag (default 10%).

    Returns:
        Dict with ``throughput_change`` (float), ``regression`` (bool),
        and ``memory_change`` (int, bytes).
    """
    baseline = before.pages_per_second
    change = (after.pages_per_second - baseline) / baseline if baseline else 0.0
    return {"throughput_change": change, "regression": change < -abs(regression_threshold), "memory_change": after.peak_memory_bytes - before.peak_memory_bytes}


# ---------------------------------------------------------------------------
# Benchmark profiles
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class BenchmarkProfile:
    """Predefined resource profile for benchmark runs.

    Attributes:
        name: Profile identifier.
        concurrency: Max concurrent requests.
        delay_seconds: Per-host delay between requests.
        timeout_seconds: Request timeout.
        max_pages: Maximum pages to fetch (caps run duration).
    """

    name: str
    concurrency: int
    delay_seconds: float
    timeout_seconds: int
    max_pages: int


PROFILES: dict[str, BenchmarkProfile] = {
    "low": BenchmarkProfile("low", concurrency=1, delay_seconds=2.0, timeout_seconds=30, max_pages=10),
    "standard": BenchmarkProfile("standard", concurrency=3, delay_seconds=1.0, timeout_seconds=25, max_pages=50),
    "high": BenchmarkProfile("high", concurrency=8, delay_seconds=0.3, timeout_seconds=20, max_pages=200),
}


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

class BenchmarkRunner:
    """Run pipeline benchmarks with resource measurement.

    The runner executes a crawl configuration under a given profile and
    collects throughput, memory, and error metrics.  Results can be
    stored in a :class:`BenchmarkHistory` for regression detection.

    Example::

        runner = BenchmarkRunner()
        result = runner.run("standard", config_path="configs/news.yaml")
        print(result.pages_per_second)
    """

    def __init__(self, *, profiles: dict[str, BenchmarkProfile] | None = None) -> None:
        self._profiles = profiles or PROFILES

    def run(self, profile_name: str, *, config_path: str | Path, resume: bool = False) -> BenchmarkResult:
        """Execute a single benchmark run.

        Args:
            profile_name: Key into the profiles dict (``"low"``,
                ``"standard"``, ``"high"``).
            config_path: Path to the YAML crawl configuration.
            resume: If *True*, resume an interrupted run.

        Returns:
            :class:`BenchmarkResult` with measured metrics.

        Raises:
            KeyError: If *profile_name* is not a known profile.
        """
        profile = self._profiles[profile_name]
        start = time.monotonic()
        peak_mem = _peak_rss()
        bytes_xfer = 0
        errors = 0
        pages = 0

        try:
            from .application_service import ApplicationService

            service = ApplicationService(config_path)
            result = service.run(resume=resume)
            pages = int(result.get("stats", {}).get("responses", 0))
            errors = int(result.get("stats", {}).get("errors", 0))
            bytes_xfer = int(result.get("stats", {}).get("bytes_transferred", 0))
        except Exception as exc:
            errors += 1
            _benchmark_logger.warning("Benchmark run failed: %s", exc)

        elapsed = time.monotonic() - start
        peak_mem = max(peak_mem, _peak_rss())

        return BenchmarkResult(
            profile=profile.name,
            pages=pages,
            duration_seconds=round(elapsed, 3),
            peak_memory_bytes=peak_mem,
            bytes_transferred=bytes_xfer,
            errors=errors,
        )

    def run_all(self, *, config_path: str | Path) -> list[BenchmarkResult]:
        """Run benchmarks for all registered profiles sequentially.

        Args:
            config_path: Path to the YAML crawl configuration.

        Returns:
            List of :class:`BenchmarkResult`, one per profile.
        """
        return [self.run(name, config_path=config_path) for name in self._profiles]


# ---------------------------------------------------------------------------
# Benchmark history
# ---------------------------------------------------------------------------

class BenchmarkHistory:
    """Persist and query historical benchmark results as JSON.

    Results are stored in a JSON file, one entry per run.  The history
    supports querying the latest result for a profile and detecting
    regressions against a baseline.

    Args:
        path: Path to the JSON history file.  Created on first save.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._results: list[dict[str, object]] = []
        self._load()

    def _load(self) -> None:
        if self.path.is_file():
            try:
                data = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._results = data
            except (json.JSONDecodeError, OSError):
                self._results = []

    def save(self) -> None:
        """Persist all stored results to the history file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._results, ensure_ascii=False, indent=2), encoding="utf-8")

    def add(self, result: BenchmarkResult) -> None:
        """Append a result and auto-save."""
        self._results.append(result.to_mapping())
        self.save()

    def latest(self, profile: str) -> BenchmarkResult | None:
        """Return the most recent result for *profile*, or *None*."""
        for entry in reversed(self._results):
            if entry.get("profile") == profile:
                return _dict_to_result(entry)
        return None

    def baseline(self, profile: str) -> BenchmarkResult | None:
        """Return the first recorded result for *profile* as baseline."""
        for entry in self._results:
            if entry.get("profile") == profile:
                return _dict_to_result(entry)
        return None

    def check_regression(self, result: BenchmarkResult, *, threshold: float = 0.1) -> dict[str, object]:
        """Compare *result* against the stored baseline for its profile.

        Args:
            result: Current benchmark result.
            threshold: Fractional throughput drop that triggers regression.

        Returns:
            Comparison dict from :func:`compare_benchmark`.  If no
            baseline exists, returns ``{"regression": False, "reason": "no_baseline"}``.
        """
        baseline = self.baseline(result.profile)
        if baseline is None:
            return {"regression": False, "reason": "no_baseline"}
        return compare_benchmark(baseline, result, regression_threshold=threshold)

    def all_results(self) -> list[dict[str, object]]:
        """Return all stored results as a list of dicts."""
        return list(self._results)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _peak_rss() -> int:
    """Return current process RSS in bytes, or 0 if unavailable."""
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss
    except (ImportError, OSError):
        return 0


def _dict_to_result(entry: dict[str, Any]) -> BenchmarkResult:
    """Reconstruct a BenchmarkResult from a stored dict."""
    return BenchmarkResult(
        profile=str(entry.get("profile", "")),
        pages=int(entry.get("pages", 0)),
        duration_seconds=float(entry.get("duration_seconds", 0.0)),
        peak_memory_bytes=int(entry.get("peak_memory_bytes", 0)),
        bytes_transferred=int(entry.get("bytes_transferred", 0)),
        errors=int(entry.get("errors", 0)),
    )


_benchmark_logger = logging.getLogger("omnicrawler.benchmarking")

