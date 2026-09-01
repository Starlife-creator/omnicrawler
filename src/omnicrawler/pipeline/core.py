from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from ..core.config import AppConfig
from ..quality.diagnostics import DiagnosticRecorder
from ..runtime.resources import ResourceGuard
from ..runtime.run_control import RunControl
from ..security.egress import EgressBroker
from ..security.policy import HostRateLimiter, RobotsPolicy, ScopePolicy
from ..services.metrics import RunMetrics
from ..services.record_sinks import RecordSinkManager, build_record_sink_manager
from ..services.regression_library import RegressionLibrary
from ..services.storage_backends import ObjectStore, build_object_store
from ..state import StateStore
from ..templates.template_monitor import TemplateMonitor
from ._builders import _PipelineBuilders
from ._exports import _PipelineExports
from ._extract import _PipelineExtract
from ._fetch import _PipelineFetch
from ._run import _PipelineRun
from .registry import build_registry

LOGGER = logging.getLogger("omnicrawler")


@dataclass(slots=True)
class PipelineDependencies:
    """Optional high-cost resources supplied by an application composition root.

    Injected resources remain owned by the caller unless ``close_injected`` is
    true.  Resources built by :class:`Pipeline` are always managed by it.  This
    keeps ``Pipeline(config)`` backward compatible while enabling lightweight
    tests and embedding without hidden database/storage construction.
    """

    egress: EgressBroker | None = None
    state: StateStore | None = None
    object_store: ObjectStore | None = None
    record_sinks: RecordSinkManager | None = None
    close_injected: bool = False


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

    def __init__(
        self,
        config: AppConfig,
        *,
        dependencies: PipelineDependencies | None = None,
    ) -> None:
        """Initialize all pipeline subsystems from the given configuration.

        Args:
            config: Fully-resolved application configuration.
            dependencies: Optional externally constructed high-cost resources.
                Injected resources are not closed unless ``close_injected`` is
                explicitly enabled.

        Raises:
            Exception: 任一子系统构造失败时，已建资源全部释放后重新抛出。
        """
        from contextlib import ExitStack

        self.config = config
        self.workspace = config.workspace
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._close_stack = ExitStack()
        dependencies = dependencies or PipelineDependencies()

        def track(resource: Any) -> Any:
            self._close_stack.callback(self._try_close, resource)
            return resource

        try:
            self.egress = (
                dependencies.egress
                if dependencies.egress is not None
                else EgressBroker(config)
            )
            self.registry = track(build_registry(config, self.egress))
            state_injected = dependencies.state is not None
            self._manage_state = not state_injected or dependencies.close_injected
            state = (
                dependencies.state
                if dependencies.state is not None
                else StateStore(self.workspace / "state.sqlite3")
            )
            self.state = track(state) if self._manage_state else state
            self.registry.bind_plugin_runtime(config=config, state_store=self.state)
            object_store_injected = dependencies.object_store is not None
            object_store = (
                dependencies.object_store
                if dependencies.object_store is not None
                else build_object_store(config, self.egress)
            )
            self.object_store = (
                track(object_store)
                if not object_store_injected or dependencies.close_injected
                else object_store
            )
            sinks_injected = dependencies.record_sinks is not None
            self._manage_record_sinks = not sinks_injected or dependencies.close_injected
            sinks = (
                dependencies.record_sinks
                if dependencies.record_sinks is not None
                else build_record_sink_manager(config, self.egress)
            )
            self.record_sinks = track(sinks) if self._manage_record_sinks else sinks
            self.metrics = RunMetrics()
            self.source = self.registry.sources[config.source_kind](config)
            self.scope = ScopePolicy(config)
            self.robots = RobotsPolicy(config, egress=self.egress)
            self.limiter = HostRateLimiter(float(config.section("http").get("delay_seconds", 1)))
            self.diagnostics = DiagnosticRecorder(self.workspace, config.raw)
            self._local = threading.local()
            self._shared_fetchers: dict[str, Any] = {}
            self._shared_fetchers_lock = threading.Lock()
            # S2.5.45：线程局部 fetcher 注册表——close 时统一回收
            self._all_fetchers: list[Any] = []
            self._all_fetchers_lock = threading.Lock()
            self._processor_instances: dict[str, Any] = {}
            self._processor_lock = threading.Lock()  # S2.5.41：实例创建互斥
            # S4.5 P3#135：插件 hook_fail_open 只读一次，_emit 不再每事件重读配置
            self._hook_fail_open = bool(config.section("plugins").get("hook_fail_open", False))
            self._executor: ThreadPoolExecutor | None = None
            self.resource_guard = ResourceGuard(config)
            self._auth_provider = self._build_auth_provider()
            self._transformers = self._build_transformers()
            self.template_monitor = TemplateMonitor(config)
            self._api_discoveries: list[dict[str, Any]] = []
            self.run_control = RunControl(self.workspace)
            self.regression_library = RegressionLibrary(config)
        except Exception:
            self._close_stack.close()  # S1.5.2：构造中途失败，回滚已建资源
            raise

    @staticmethod
    def _try_close(resource: Any) -> None:
        """单项关闭，失败不阻断其余资源回收。"""
        close = getattr(resource, "close", None)
        if callable(close):
            try:
                close()
            except Exception as exc:  # noqa: BLE001 - 关闭失败不应中断整体
                LOGGER.warning("关闭资源失败 %r: %s", resource, exc)

    def close(self) -> None:
        """Release shared fetchers, the thread pool, record sinks, and state."""
        errors: list[Exception] = []
        # S2.5.45：线程局部 fetcher 也在 close 时统一回收（消除连接池泄漏）
        fetchers = list(self._shared_fetchers.values())
        with self._all_fetchers_lock:
            fetchers.extend(self._all_fetchers)
            self._all_fetchers.clear()
        for fetcher in fetchers:
            try:
                close = getattr(fetcher, "close", None)
                if callable(close):
                    close()
            except Exception as exc:  # noqa: BLE001 - 单项关闭隔离
                errors.append(exc)
        if self._executor is not None:
            try:
                self._executor.shutdown(wait=False)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
            self._executor = None
        if self._manage_record_sinks:
            try:
                self.record_sinks.close()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        if self._manage_state:
            try:
                self.state.close()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        stack = getattr(self, "_close_stack", None)
        if stack is not None:
            try:
                stack.close()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)
        if errors:
            raise RuntimeError("Pipeline 关闭阶段存在异常: " + "; ".join(str(e) for e in errors)) from errors[0]

    def __enter__(self) -> Pipeline:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    def _get_executor(self, concurrency: int) -> ThreadPoolExecutor:
        """Return a reusable thread pool, creating it on first use."""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=concurrency, thread_name_prefix="omnicrawler"
            )
        return self._executor

    def _emit(self, event: str, **context: Any) -> list[Any]:
        return self.registry.emit(
            event,
            fail_open=self._hook_fail_open,
            pipeline=self,
            **context,
        )
