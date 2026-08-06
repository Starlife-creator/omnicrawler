"""Base class declaring all ``Pipeline`` instance attributes for mixin type-checking.

The mixin classes (:mod:`._builders`, :mod:`._exports`, :mod:`._fetch`,
:mod:`._run`, :mod:`._extract`) inherit from :class:`_PipelineBase` so that
``self.<attr>`` accesses type-check without error.  The data attributes mirror
the assignments performed in :meth:`Pipeline.__init__`.

Because each mixin only inherits from :class:`_PipelineBase`, cross-mixin
method calls (e.g. ``self._emit`` invoked from the fetch mixin) would normally
trigger mypy ``attr-defined`` errors.  To keep :class:`_PipelineBase` free of
method definitions (attribute declarations only) while still allowing those
calls to type-check, the relevant methods are declared here as ``Callable``
class-level annotations.  These are *attributes*, not methods (no ``def``
bodies); the concrete implementations live in the mixin modules and in
:class:`Pipeline` itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import threading
    from collections.abc import Callable
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    from ..core.config import AppConfig
    from ..core.models import FetchResult
    from ..plugins.plugins import Registry
    from ..quality.diagnostics import DiagnosticRecorder
    from ..runtime.resources import ResourceGuard
    from ..runtime.run_control import RunControl
    from ..security.egress import EgressBroker
    from ..security.policy import HostRateLimiter, RobotsPolicy, ScopePolicy
    from ..services.metrics import RunMetrics
    from ..services.record_sinks import RecordSinkManager
    from ..services.regression_library import RegressionLibrary
    from ..services.storage_backends import ObjectStore
    from ..state import StateStore
    from ..templates.template_monitor import TemplateMonitor


class _PipelineBase:
    """Declare all instance attributes so mixin methods type-check cleanly."""

    # --- Data attributes (assigned in Pipeline.__init__) ---
    config: AppConfig
    workspace: Path
    egress: EgressBroker
    registry: Registry
    state: StateStore
    object_store: ObjectStore
    record_sinks: RecordSinkManager
    metrics: RunMetrics
    source: Any
    scope: ScopePolicy
    robots: RobotsPolicy
    limiter: HostRateLimiter
    diagnostics: DiagnosticRecorder
    _local: threading.local
    _shared_fetchers: dict[str, Any]
    _shared_fetchers_lock: threading.Lock
    _processor_instances: dict[str, Any]
    _processor_lock: threading.Lock
    _all_fetchers: list[Any]
    _all_fetchers_lock: threading.Lock
    _executor: ThreadPoolExecutor | None
    resource_guard: ResourceGuard
    _auth_provider: Any | None
    _transformers: list[Any]
    template_monitor: TemplateMonitor
    _api_discoveries: list[dict[str, Any]]
    run_control: RunControl
    regression_library: RegressionLibrary

    # --- Method signatures (Callable attributes) ----------------------------
    # Declared so cross-mixin ``self.<method>(...)`` calls type-check.  The
    # concrete implementations live in the mixin modules / Pipeline.  Using
    # ``Callable[..., <RetType>]`` keeps the declarations permissive on
    # arguments while preserving the return type for callers.
    _emit: Callable[..., list[Any]]
    _get_executor: Callable[..., ThreadPoolExecutor]
    _processor: Callable[..., Any]
    _run_exports: Callable[..., dict[str, Any]]
    _stage_exports: Callable[..., dict[str, Any]]
    _write_pipeline_summary: Callable[..., dict[str, Any]]
    _thread_fetcher: Callable[..., Any]
    _fetch_checked: Callable[..., FetchResult]
    _save_artifact: Callable[..., Path]
    _handle_result: Callable[..., None]
