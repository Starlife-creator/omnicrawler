"""Repeatable benchmark measurements and historical comparison.

Provides benchmark profiles, a runner that measures pipeline throughput,
and historical baseline comparison for performance regression detection.

2026-09-11 修正（对应 §1.2 采集能力升级项「可复现的任务基准与公平对比」）：

* **profile 真正生效**：此前 profile 只作为标签记录，`concurrency` / `delay` /
  `timeout` / `max_pages` 从未写入运行配置——三个 profile 跑的是同一份负载，
  结果却被当作不同 profile 归档，既不公平也不可比。现由 :func:`apply_profile`
  派生一份实际执行的配置。
* **结果自带复现信息**：配置指纹、profile 参数、包版本与平台随结果一同记录，
  使「同一配置 + 同一 profile → 可比较的结果」可被第三方验证。
* **失败运行不再污染基线**：失败会被标记（``ok=False``），且不参与基线选取。
  此前失败以 ``pages=0`` 入库并成为基线，使该 profile 的退化检测静默失效。
* **指标不再恒为 0**：``ApplicationService.run`` 返回的是展平摘要，旧代码却读
  ``result["stats"]["responses"]``（不存在该子键）→ ``pages`` / ``errors`` 恒为 0，
  ``pages_per_second`` 因此**一直是 0.0**：基准从未真正测到过吞吐量。
  现按真实结构读取，并把 ``bytes_transferred`` 改为从状态库聚合落库字节数
  （流水线摘要本就不含该字段，旧值同样是恒 0）。
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import statistics
import time
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..core.config import AppConfig


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
        ok: 运行终态是否可采信（``succeeded`` 或 ``partial_success``）。
            ``partial_success`` 意味着有交付但存在错误记录，吞吐量依然是一次
            有效测量。它不判断能不能当作测量——那由 :attr:`usable` 负责。
        status: 流水线终态（``succeeded`` / ``failed`` / ``cancelled``）；
            抛错时为空串。便于仅凭历史文件定位失败原因。
        config_sha256: 源配置内容的 SHA-256（空字符串表示读取失败）。
        effective_config_sha256: 施加 profile 后实际执行的那份配置的 SHA-256。
        profile_settings: 该次运行所用的档位参数（name/concurrency/delay/
            timeout/max_pages），用于确定性重建派生配置。
        environment: 包版本、Python 版本、平台与解释器路径——比较两次结果前
            必须先对齐这些，否则数字没有可比性。
    """

    profile: str
    pages: int
    duration_seconds: float
    peak_memory_bytes: int
    bytes_transferred: int
    errors: int
    # --- 可复现与公平性元数据（2026-09-11 新增，见 docs/BENCHMARKING.md）---
    ok: bool = True
    status: str = ""
    config_sha256: str = ""
    effective_config_sha256: str = ""
    profile_settings: tuple[tuple[str, str], ...] = ()
    environment: tuple[tuple[str, str], ...] = ()

    @property
    def pages_per_second(self) -> float:
        return self.pages / self.duration_seconds if self.duration_seconds else 0.0

    @property
    def seconds_per_thousand_pages(self) -> float:
        return self.duration_seconds * 1000 / self.pages if self.pages else 0.0

    @property
    def usable(self) -> bool:
        """能否作为一次基准测量（也即能否成为基线）。

        两个条件缺一不可：

        * ``ok`` —— 运行本身成功结束；
        * ``pages > 0`` —— 确实取到了页面。吞吐量是「页 / 秒」，一页都没取到时
          这个比值无从谈起，入库只会污染基线与退化检测。

        实测（2026-09-11）：种子全部连接失败时流水线仍以 ``succeeded`` 结束、
        ``pages=0``、``errors=0``；若只按 ``ok`` 判定，这种空白运行会成为基线，
        使该档位的退化检测从此形同虚设。

        ``ok`` 与 ``status`` 要求**同时**成立：两者是独立字段，手工构造的结果对象
        完全可能给出互相矛盾的组合（``status="failed"`` 而 ``ok`` 取默认 ``True``）。
        与其信任调用方自觉，不如在这里把一致性当作判据的一部分。
        """
        return self.ok and self.status in _USABLE_STATUSES and self.pages > 0

    def to_mapping(self) -> dict[str, object]:
        return {
            **asdict(self),
            "profile_settings": dict(self.profile_settings),
            "environment": dict(self.environment),
            "pages_per_second": self.pages_per_second,
            "seconds_per_thousand_pages": self.seconds_per_thousand_pages,
        }


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

#: 派生配置的存放子目录，相对被跑分的配置所在目录。刻意用固定路径：复现一次基准时，
#: 执行的是哪份配置应当可以被直接打开查看，而不是藏在随机临时目录里。
_CONFIG_SUBDIR = ".benchmark"
#: 运行期采样 RSS 的最小间隔（秒）。逐事件调用 psutil 本身会污染被测负载。
_RSS_SAMPLE_INTERVAL = 0.05

#: 能被基准采信的终态。`partial_success` 表示「有交付但存在错误记录」——
#: 它确实交付了页面，吞吐量因此仍是一次有效测量，不应被排除在基线之外。
#: 2026-09-11：流水线开始发出该终态（此前从不发出），故此处同步放宽 `ok`。
_OK_STATUSES = frozenset({"succeeded", "partial_success"})
#: `usable` 额外接受空状态——2026-09-11 之前写入的历史条目没有 `status` 字段，
#: 无法回溯判定，只能沿用其 `ok` 字段。
_USABLE_STATUSES = _OK_STATUSES | {""}


def apply_profile(config_raw: Mapping[str, Any], profile: BenchmarkProfile) -> dict[str, Any]:
    """Return a copy of *config_raw* with *profile*'s resource limits applied.

    这是「profile 到底意味着什么」的唯一真源。映射是确定性的：任何人只要持有源配置与
    结果里记录的 ``profile_settings``，都能重新生成某次运行所用的确切配置——
    这正是结果可比、可复核的前提。

    Args:
        config_raw: 原始配置映射（通常是 ``AppConfig.raw``）。
        profile: 要施加的资源档位。

    Returns:
        深拷贝后的新映射，``crawl`` / ``http`` 段已按 profile 覆写。

    Raises:
        ValueError: ``crawl`` 或 ``http`` 段存在但不是映射。
    """
    data = copy.deepcopy(dict(config_raw))
    crawl = data.setdefault("crawl", {})
    http = data.setdefault("http", {})
    if not isinstance(crawl, dict) or not isinstance(http, dict):
        raise ValueError("配置的 crawl / http 段必须是映射，无法套用 benchmark profile")
    crawl["concurrency"] = profile.concurrency
    crawl["max_pages"] = profile.max_pages
    http["delay_seconds"] = profile.delay_seconds
    http["timeout_seconds"] = profile.timeout_seconds
    return data


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

    def run(self, profile_name: str, *, config_path: str | Path, resume: bool = False,
            workdir: str | Path | None = None) -> BenchmarkResult:
        """Execute a single benchmark run under *profile_name*.

        档位会被**真正施加**：``concurrency`` / ``delay`` / ``timeout`` / ``max_pages``
        写入一份派生配置，本次运行针对该配置执行（见 :func:`apply_profile`）。
        2026-09-11 之前这里只记录 ``profile.name``，三个档位跑的是同一份负载。

        Args:
            profile_name: Key into the profiles dict (``"low"``,
                ``"standard"``, ``"high"``).
            config_path: Path to the YAML crawl configuration.
            resume: If *True*, resume an interrupted run.
            workdir: 派生配置的存放目录。默认 ``<配置目录>/.benchmark``；
                派生配置会保留以便复核与复现。

        Returns:
            :class:`BenchmarkResult` with measured metrics and the metadata
            needed to reproduce and compare the run.

        Raises:
            KeyError: If *profile_name* is not a known profile.
            ValueError: 源配置无法加载或结构不合法（属用法错误，不记为一次测量）。
        """
        profile = self._profiles[profile_name]
        source = Path(config_path).expanduser().resolve()
        effective, source_config = self._materialize_profile_config(source, profile, workdir=workdir)

        started = time.monotonic()
        peak = _peak_rss()
        last_sample = started

        def _sample_rss(_event: str, _payload: dict[str, Any]) -> None:
            # 采样被节流：逐事件调用 psutil 会把基准本身拖慢。
            nonlocal peak, last_sample
            now = time.monotonic()
            if now - last_sample < _RSS_SAMPLE_INTERVAL:
                return
            last_sample = now
            peak = max(peak, _peak_rss())

        bytes_xfer = 0
        errors = 0
        pages = 0
        status = ""

        try:
            from .application_service import ApplicationService

            service = ApplicationService(effective)
            result = service.run(resume=resume, callback=_sample_rss)
            status = str(result.get("status", ""))
            stats = _summary_stats(result)
            run_id = str(result.get("run_id", ""))
            pages = int(stats.get("responses", 0) or 0)
            errors = int(stats.get("errors", 0) or 0)
            bytes_xfer = _stored_bytes(source_config.workspace, run_id)
        except Exception as exc:
            errors += 1
            _benchmark_logger.warning("Benchmark run failed: %s", exc)

        # ok 只陈述「这次运行的终态是否可采信」；「能不能当测量」由
        # BenchmarkResult.usable 判定（ok ∧ pages > 0）—— 两件事分开表达，
        # 失败原因才不会被混淆。
        ok = status in _OK_STATUSES
        if not ok:
            _benchmark_logger.warning(
                "Benchmark run did not succeed: status=%r pages=%d errors=%d",
                status or "<exception>", pages, errors,
            )
        elif pages <= 0:
            _benchmark_logger.warning(
                "Benchmark run produced no pages despite status=succeeded "
                "(errors=%d) —— 该次运行不可作为基准",
                errors,
            )

        elapsed = time.monotonic() - started
        peak = max(peak, _peak_rss())

        return BenchmarkResult(
            profile=profile.name,
            pages=pages,
            duration_seconds=round(elapsed, 3),
            peak_memory_bytes=peak,
            bytes_transferred=bytes_xfer,
            errors=errors,
            ok=ok,
            status=status,
            config_sha256=_sha256_file(source),
            effective_config_sha256=_sha256_file(effective),
            profile_settings=_profile_settings(profile),
            environment=_environment(),
        )

    def run_all(self, *, config_path: str | Path) -> list[BenchmarkResult]:
        """Run benchmarks for all registered profiles sequentially.

        Args:
            config_path: Path to the YAML crawl configuration.

        Returns:
            List of :class:`BenchmarkResult`, one per profile.
        """
        return [self.run(name, config_path=config_path) for name in self._profiles]

    def _materialize_profile_config(self, source: Path, profile: BenchmarkProfile,
                                    *, workdir: str | Path | None) -> tuple[Path, AppConfig]:
        """写入「源配置 + profile 覆写」后的派生配置。

        Returns:
            ``(派生配置路径, 源配置对象)``。返回源配置对象是为了让调用方拿到
            工作区位置（聚合落库字节数需要它），避免重复加载。
        """
        import yaml

        from ..core.config import load_config

        config = load_config(source)
        data = apply_profile(config.raw, profile)
        base = Path(workdir) if workdir is not None else source.parent / _CONFIG_SUBDIR
        target = base / f"profile-{profile.name}.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        return target, config


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
        """Return the first *successful* recorded result for *profile*.

        刻意跳过**不可用**的运行（:attr:`BenchmarkResult.usable` 为假）：这类运行
        以 ``pages=0`` 入库、吞吐量为 0；若被当作基线，之后任何真实运行都会显得
        「比基线更好」，该档位的退化检测就此静默失效。2026-09-11 之前正是如此，
        当时连抛错的失败运行都会被计入。
        """
        for entry in self._results:
            if entry.get("profile") != profile:
                continue
            candidate = _dict_to_result(entry)
            if candidate.usable:
                return candidate
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

def _summary_stats(result: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return the metric mapping inside a pipeline summary.

    2026-09-11 修正：``ApplicationService.run`` 返回的是**展平**的摘要
    （``run_id`` / ``status`` / ``processed`` / ``responses`` / ``records`` / …），
    并不存在 ``stats`` 子字典。旧代码读 ``result["stats"]["responses"]`` 因而恒取到 0。
    这里以展平结构为准，同时兼容将来可能引入的 ``stats`` 子字典，
    避免又一次「读错了键但一声不响」。
    """
    nested = result.get("stats")
    return nested if isinstance(nested, Mapping) else result


def _stored_bytes(workspace: Path | None, run_id: str) -> int:
    """Sum stored response sizes for *run_id*; ``0`` when unavailable.

    流水线摘要不含 ``bytes_transferred``，旧代码因此长期记录 0。直接从状态库聚合
    真实落库字节数——它衡量的是「实际保存下来的数据量」，正是基准需要的口径。
    """
    if workspace is None or not run_id:
        return 0
    database = Path(workspace) / "state.sqlite3"
    if not database.is_file():
        return 0
    import sqlite3
    from contextlib import closing

    try:
        uri = database.resolve().as_uri() + "?mode=ro"
        # 必须 closing()：`with sqlite3.connect(...)` 只管事务、**不关闭连接**，
        # 在 Windows 上会一直占着文件句柄（实测：临时目录无法清理）。
        with closing(sqlite3.connect(uri, uri=True)) as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(size_bytes), 0) FROM responses WHERE run_id = ?",
                (run_id,),
            ).fetchone()
    except sqlite3.Error as exc:
        _benchmark_logger.warning("无法读取落库字节数: %s", exc)
        return 0
    return int(row[0]) if row and row[0] is not None else 0


def _sha256_file(path: Path) -> str:
    """Return the SHA-256 of *path*, or ``""`` if it cannot be read."""
    digest = hashlib.sha256()
    try:
        digest.update(path.read_bytes())
    except OSError:
        return ""
    return digest.hexdigest()


def _profile_settings(profile: BenchmarkProfile) -> tuple[tuple[str, str], ...]:
    """Flatten a profile into comparable, JSON-friendly pairs."""
    return (
        ("name", profile.name),
        ("concurrency", str(profile.concurrency)),
        ("delay_seconds", str(profile.delay_seconds)),
        ("timeout_seconds", str(profile.timeout_seconds)),
        ("max_pages", str(profile.max_pages)),
    )


def _environment() -> tuple[tuple[str, str], ...]:
    """Record what two results must agree on before they can be compared."""
    import platform
    import sys

    try:
        from .. import __version__ as package_version
    except ImportError:  # pragma: no cover - 仅在打包异常时发生
        package_version = "unknown"
    return (
        ("package_version", str(package_version)),
        ("python", platform.python_version()),
        ("implementation", platform.python_implementation()),
        ("platform", platform.platform()),
        ("executable", sys.executable),
    )


def _pairs(value: object) -> tuple[tuple[str, str], ...]:
    """Accept dict / list-of-pairs (JSON round-trip) and return str pairs."""
    if isinstance(value, dict):
        return tuple((str(key), str(item)) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return tuple(
            (str(item[0]), str(item[1]))
            for item in value
            if isinstance(item, (list, tuple)) and len(item) == 2
        )
    return ()


def _peak_rss() -> int:
    """Return current process RSS in bytes, or 0 if unavailable."""
    try:
        import psutil
        return psutil.Process(os.getpid()).memory_info().rss
    except (ImportError, OSError):
        return 0


def _dict_to_result(entry: dict[str, Any]) -> BenchmarkResult:
    """Reconstruct a BenchmarkResult from a stored dict.

    向后兼容：2026-09-11 之前写入的历史条目没有新字段，此时 ``ok`` 取 ``True``、
    元数据为空——这是诚实的默认（无法回溯判定旧条目是否失败），也让旧历史文件继续可读。
    """
    return BenchmarkResult(
        profile=str(entry.get("profile", "")),
        pages=int(entry.get("pages", 0)),
        duration_seconds=float(entry.get("duration_seconds", 0.0)),
        peak_memory_bytes=int(entry.get("peak_memory_bytes", 0)),
        bytes_transferred=int(entry.get("bytes_transferred", 0)),
        errors=int(entry.get("errors", 0)),
        ok=bool(entry.get("ok", True)),
        status=str(entry.get("status", "")),
        config_sha256=str(entry.get("config_sha256", "")),
        effective_config_sha256=str(entry.get("effective_config_sha256", "")),
        profile_settings=_pairs(entry.get("profile_settings")),
        environment=_pairs(entry.get("environment")),
    )


_benchmark_logger = logging.getLogger("omnicrawler.benchmarking")

