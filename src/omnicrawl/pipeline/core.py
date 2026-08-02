from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from ..core.config import AppConfig
from ..quality.diagnostics import DiagnosticRecorder
from ..runtime.resources import ResourceGuard
from ..runtime.run_control import RunControl
from ..security.egress import EgressBroker
from ..security.policy import HostRateLimiter, RobotsPolicy, ScopePolicy
from ..services.metrics import RunMetrics
from ..services.record_sinks import build_record_sink_manager
from ..services.regression_library import RegressionLibrary
from ..services.storage_backends import build_object_store
from ..state import StateStore
from ..templates.template_monitor import TemplateMonitor
from ._builders import _PipelineBuilders
from ._exports import _PipelineExports
from ._extract import _PipelineExtract
from ._fetch import _PipelineFetch
from ._run import _PipelineRun
from .registry import build_registry

LOGGER = logging.getLogger("omnicrawl")


class Pipeline(_PipelineBuilders, _PipelineExports, _PipelineFetch, _PipelineRun, _PipelineExtract):
    """Nine-stage crawl orchestration engine with per-URL exception isolation.

    Stages: Setup, Fetch, Extract, Discover, PDF, Quality, Export, plus
    stream-mode and reprocess entry points.  Each stage is wrapped in
    try/except so a single URL failure never aborts the run; errors are
    classified as retryable or permanent, recorded in state, and surfaced
    via diagnostics and lifecycle hooks.  Checkpoints persisted after every
    stage allow ``resume=True`` to continue an interrupted run, and
    ``retry_failed=True`` to re-queue previously failed requests.
    """

    def __init__(self, config: AppConfig) -> None:
        """Initialize all pipeline subsystems from the given configuration.

        Args:
            config: Fully-resolved application configuration.
        """
        self.config = config
        self.workspace = config.workspace
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.egress = EgressBroker(config)
        self.registry = build_registry(config, self.egress)
        self.state = StateStore(self.workspace / "state.sqlite3")
        self.object_store = build_object_store(config, self.egress)
        self.record_sinks = build_record_sink_manager(config, self.egress)
        self.metrics = RunMetrics()
        self.source = self.registry.sources[config.source_kind](config)
        self.scope = ScopePolicy(config)
        self.robots = RobotsPolicy(config, egress=self.egress)
        self.limiter = HostRateLimiter(float(config.section("http").get("delay_seconds", 1)))
        self.diagnostics = DiagnosticRecorder(self.workspace, config.raw)
        self._local = threading.local()
        self._shared_fetchers: dict[str, Any] = {}
        self._shared_fetchers_lock = threading.Lock()
        self._processor_instances: dict[str, Any] = {}
        self._executor: ThreadPoolExecutor | None = None
        self.resource_guard = ResourceGuard(config)
        self._auth_provider = self._build_auth_provider()
        self._transformers = self._build_transformers()
        self.template_monitor = TemplateMonitor(config)
        self._api_discoveries: list[dict[str, Any]] = []
        self.run_control = RunControl(self.workspace)
        self.regression_library = RegressionLibrary(config)

    def close(self) -> None:
        """Release shared fetchers, the thread pool, record sinks, and state."""
        for fetcher in self._shared_fetchers.values():
            close = getattr(fetcher, "close", None)
            if callable(close):
                close()
        if self._executor is not None:
            self._executor.shutdown(wait=False)
            self._executor = None
        try:
            self.record_sinks.close()
        finally:
            self.state.close()

    def __enter__(self) -> Pipeline:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def _get_executor(self, concurrency: int) -> ThreadPoolExecutor:
        """Return a reusable thread pool, creating it on first use."""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=concurrency, thread_name_prefix="omnicrawl"
            )
        return self._executor

    def _emit(self, event: str, **context: Any) -> list[Any]:
        return self.registry.emit(
            event,
            fail_open=bool(self.config.section("plugins").get("hook_fail_open", False)),
            pipeline=self,
            **context,
        )
