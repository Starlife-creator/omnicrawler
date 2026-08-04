"""Run mixin: main crawl loop, reprocessing entry point and stream mode."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, as_completed, wait
from pathlib import Path
from typing import Any

from ..core.errors import PolicyBlockedError, describe_error
from ..core.models import CrawlRequest, FetchResult
from ..extraction import extractors
from ..fetching.streams import collect_sse, collect_websocket
from ..pipeline_ops.pdf_integration import run_pdf_pipeline
from ..plugins.plugin_runtime import prepare_request, transform_record
from ..runtime.resource_profiles import effective_concurrency, profile_for
from ..runtime.resources import ResourceLimitError
from ..security.policy import is_private_target
from ._mixin_base import _PipelineBase

LOGGER = logging.getLogger("omnicrawl")


class _PipelineRun(_PipelineBase):
    def run(
        self,
        *,
        resume: bool = False,
        retry_failed: bool = False,
        max_pages: int | None = None,
        run_pdf: bool | None = None,
        callback: Callable[[str, dict[str, Any]], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Execute the full crawl pipeline and return the export summary.

        Args:
            resume: Continue from the last checkpoint instead of seeding fresh.
            retry_failed: Re-enqueue previously failed requests before crawling.
            max_pages: Override ``crawl.max_pages`` for this run.
            run_pdf: Force-enable or force-disable the PDF post-processing stage.
            callback: Optional progress callback receiving ``(event, details)``.
            should_stop: Optional predicate; returning ``True`` cancels the run.

        Returns:
            The assembled run summary dict, including export results.
        """
        if self.config.source_kind in {"websocket", "sse", "long_poll"}:
            return self._run_stream()
        if self.config.source_kind == "redis":
            raise RuntimeError("Redis分布式模式请使用 omnicrawl.redis_frontier.RedisFrontier；参见 docs/DISTRIBUTED.md")
        if self.config.source_kind == "scrapy":
            from ..sources.frameworks import run_scrapy
            return run_scrapy(self.config)

        # === Stage: Dispatch ===
        self.resource_guard.check(force=True)
        self.run_control.reset()
        self.egress.reconnect_task()
        run_id = self.state.start_run(self.config.project_name, str(self.config.path))
        setup_started = time.monotonic()
        try:
            # === Stage: Setup ===
            self._emit("before_run", run_id=run_id, resume=resume, retry_failed=retry_failed)
            updates = self.config.section("updates")
            reset_all = (
                self.config.source_kind == "incremental"
                or bool(updates.get("enabled") and updates.get("revisit_completed", True))
            ) and not resume
            self.state.prepare_cycle(reset_all=reset_all)
            if retry_failed:
                self.state.retry_failed()
            if not resume:
                for request in self.source.seed():
                    self.state.enqueue(request, force=True)
            self.state.save_checkpoint(
                run_id,
                "setup",
                "setup",
                {"resume": resume, "retry_failed": retry_failed},
            )
            self.metrics.record_stage("setup", time.monotonic() - setup_started)
        except Exception as exc:
            self.state.add_error(run_id, None, "setup", exc, retryable=False)
            self.diagnostics.failure(run_id, "setup", exc)
            self._emit("on_error", run_id=run_id, stage="setup", error=exc, request=None)
            summary = {"run_id": run_id, "status": "failed", "processed": 0, "error": str(exc)}
            self._write_pipeline_summary(summary)
            self.state.finish_run(run_id, "failed", summary)
            raise

        crawl = self.config.section("crawl")
        limit = max_pages or int(crawl.get("max_pages", 100))
        concurrency = effective_concurrency(self.config, int(crawl.get("concurrency", 4)))
        strategy = str(crawl.get("strategy", "bfs"))
        maximum_depth = int(crawl.get("max_depth", 3))
        attempts = int(self.config.section("http").get("retries", 3))
        processed = 0
        started_monotonic = time.monotonic()
        status = "succeeded"
        pdf_summary: dict[str, Any] | None = None
        try:
            # === Stage: Crawl ===
            executor = self._get_executor(max(1, concurrency))

            def control_notify(event: str, details: dict[str, Any]) -> None:
                if event == "paused":
                    self.state.transition_run(run_id, "paused", reason="user_pause")
                elif event == "resumed":
                    self.state.transition_run(run_id, "running", reason="user_resume")
                if callback:
                    callback(event, details)

            # Rolling / continuous scheduling: keep an in-flight window of up to
            # ``concurrency`` requests running at all times.  As soon as any future
            # completes we claim and submit the next request(s), eliminating the
            # per-batch barrier where fast URLs idled waiting for the slowest one.
            inflight: dict[Future[FetchResult], CrawlRequest] = {}
            frontier_exhausted = False

            def consume(future: Future[FetchResult]) -> None:
                nonlocal processed, frontier_exhausted
                request = inflight.pop(future)
                try:
                    result = future.result()
                    self._handle_result(run_id, result, maximum_depth)
                    frontier_exhausted = False  # discovery may have enqueued new URLs
                    self.state.mark_done(request.fingerprint)
                    self.state.save_checkpoint(
                        run_id,
                        "fetch",
                        request.fingerprint,
                        {"url": result.final_url, "status": result.status},
                    )
                    processed += 1
                    LOGGER.info("[%s/%s] %s %s", processed, limit, result.status, result.final_url)
                    if callback:
                        elapsed = max(0.001, time.monotonic() - started_monotonic)
                        rate = processed / elapsed
                        callback(
                            "crawl_progress",
                            {
                                "processed": processed,
                                "limit": limit,
                                "url": result.final_url,
                                "pages_per_second": round(rate, 3),
                                "eta_seconds": round((limit - processed) / rate, 1) if rate else None,
                            },
                        )
                except (PermissionError, PolicyBlockedError) as exc:
                    self.state.mark_done(request.fingerprint, status="blocked", error=str(exc))
                    self.state.add_error(run_id, request, "policy", exc, retryable=False)
                    self.diagnostics.failure(run_id, "policy", exc, request=request)
                    LOGGER.warning("已拦截 %s: %s", request.url, exc)
                    self.metrics.increment("omnicrawl_failures_total", stage="policy", error=type(exc).__name__)
                    self._emit("on_error", run_id=run_id, stage="policy", error=exc, request=request)
                except Exception as exc:  # Per-URL isolation and retry boundary.
                    info = describe_error(exc)
                    self.state.add_error(run_id, request, "fetch", exc, retryable=info.retryable)
                    self.state.mark_failed(request, exc, attempts, retryable=info.retryable)
                    self.diagnostics.failure(run_id, "fetch", exc, request=request)
                    LOGGER.error("抓取失败 %s: %s: %s", request.url, type(exc).__name__, exc)
                    self.metrics.increment("omnicrawl_failures_total", stage="fetch", error=type(exc).__name__)
                    self._emit("on_error", run_id=run_id, stage="fetch", error=exc, request=request)

            def drain() -> None:
                # Finish every in-flight request before leaving the loop so the
                # frontier never keeps orphaned ``in_progress`` rows (no lost or
                # duplicated work when the run is cancelled/stopped/aborted).
                for future in as_completed(list(inflight)):
                    consume(future)

            while inflight or (not frontier_exhausted and processed < limit):

                if not self.run_control.wait_if_paused(notify=control_notify):
                    status = "cancelled"
                    self.egress.disconnect_task()
                    drain()
                    break
                if should_stop and should_stop():
                    status = "cancelled"
                    self.run_control.request_stop()
                    self.egress.disconnect_task()
                    drain()
                    break
                try:
                    resource_snapshot = self.resource_guard.check()
                except ResourceLimitError as exc:
                    status = "failed"
                    self.state.add_error(run_id, None, "resources", exc, retryable=True)
                    self.diagnostics.failure(run_id, "resources", exc)
                    self.metrics.increment(
                        "omnicrawl_failures_total", stage="resources", error=type(exc).__name__
                    )
                    self._emit("on_error", run_id=run_id, stage="resources", error=exc, request=None)
                    drain()
                    break
                if resource_snapshot.get("disk_free_bytes") is not None:
                    self.metrics.gauge(
                        "omnicrawl_disk_free_bytes", float(resource_snapshot["disk_free_bytes"])
                    )

                # Top up the window.  ``processed + len(inflight)`` never exceeds
                # ``limit`` so successful pages cannot overshoot the cap, and each
                # claim stays bounded by the remaining concurrency budget.
                while (
                    not frontier_exhausted
                    and len(inflight) < concurrency
                    and processed + len(inflight) < limit
                ):
                    want = min(concurrency - len(inflight), limit - processed - len(inflight))
                    batch = self.state.claim(want, strategy)
                    if not batch:
                        frontier_exhausted = True
                        break
                    for request in batch:
                        inflight[executor.submit(self._fetch_checked, run_id, request)] = request

                if not inflight:
                    break

                done, _pending = wait(inflight, return_when=FIRST_COMPLETED)
                for future in done:
                    consume(future)

                frontier_stats = self.state.stats(run_id).get("frontier", {})
                self.metrics.gauge("omnicrawl_frontier_pending", float(frontier_stats.get("pending", 0)))
            crawl_status = {"processed": processed, **self.state.stats(run_id)}
            self.state.save_checkpoint(run_id, "crawl", "crawl", crawl_status)
            self.metrics.record_stage("crawl", time.monotonic() - started_monotonic)
            if callback:
                callback("crawl", crawl_status)

            # === Stage: PDF ===
            configured_pdf = self.config.section("processors").get("pdf", {}).get("enabled", False)
            pdf_enabled = configured_pdf if run_pdf is None else run_pdf
            if pdf_enabled and status != "cancelled":
                pdf_started = time.monotonic()
                pdf_summary = run_pdf_pipeline(
                    self.config,
                    self.state,
                    run_id=run_id,
                    callback=(
                        (lambda stage, value: callback(f"pdf_{stage}", value))
                        if callback
                        else None
                    ),
                    should_stop=should_stop,
                )
                self.metrics.gauge(
                    "omnicrawl_pdf_documents", float(pdf_summary.get("documents", 0))
                )
                result_summary = pdf_summary.get("result", {})
                if isinstance(result_summary, dict):
                    for key in ("processed", "succeeded", "failed", "ocr_pages", "pages"):
                        value = result_summary.get(key)
                        if isinstance(value, (int, float)):
                            self.metrics.gauge(f"omnicrawl_pdf_{key}", float(value))
                self.state.save_checkpoint(run_id, "pdf", "pdf", pdf_summary)
                self.metrics.record_stage("pdf", time.monotonic() - pdf_started)
            # === Stage: Export ===
            exported = self._stage_exports(run_id, status, processed, pdf_summary, callback)
            return exported
        except KeyboardInterrupt:
            status = "cancelled"
            self.run_control.request_stop()
            self.egress.disconnect_task()
            drain()  # E5：与 cancel/stop/资源超限分支一致，收尾在途请求防孤儿 in_progress
            summary = {"run_id": run_id, "status": status, "processed": processed, **self.state.stats(run_id)}
            self._write_pipeline_summary(summary)
            self.state.finish_run(run_id, status, summary)
            raise
        except Exception as exc:
            status = "failed"
            self.state.add_error(run_id, None, "pipeline", exc, retryable=False)
            self.diagnostics.failure(run_id, "pipeline", exc)
            self._emit("on_error", run_id=run_id, stage="pipeline", error=exc, request=None)
            summary = {"run_id": run_id, "status": status, "processed": processed, "error": str(exc), **self.state.stats(run_id)}
            self._write_pipeline_summary(summary)
            self.state.finish_run(run_id, status, summary)
            raise

    def reprocess_records(
        self,
        run_id: str | None = None,
        *,
        callback: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Re-run extraction, quality and export from archived responses without fetching."""

        reprocess_started = time.monotonic()
        if run_id is None:
            latest = self.state.latest_run()
            run_id = str(latest["run_id"]) if latest else ""
        if not run_id:
            raise ValueError("No previous run is available for reprocessing")
        rows = self.state.rows(
            "SELECT request_fingerprint, url, final_url, status_code, content_type, "
            "raw_path, elapsed_seconds FROM responses WHERE run_id=? AND raw_path IS NOT NULL "
            "ORDER BY id",
            (run_id,),
        )
        if not rows:
            raise ValueError("No archived responses are available; enable incremental.archive_raw")
        archived = {str(row["request_fingerprint"]) for row in rows}
        existing = {
            str(row["request_fingerprint"])
            for row in self.state.rows(
                "SELECT DISTINCT request_fingerprint FROM records WHERE run_id=?", (run_id,)
            )
        }
        missing_archives = existing - archived
        if missing_archives:
            raise RuntimeError(
                f"Cannot safely reset extraction: {len(missing_archives)} record sources lack raw archives"
            )
        prepared: list[tuple[dict[str, Any], Path]] = []
        workspace = self.workspace.resolve()
        for row in rows:
            path = Path(str(row["raw_path"])).resolve()
            if workspace not in path.parents or not path.is_file() or path.is_symlink():
                raise RuntimeError(f"Archived response is unavailable or outside workspace: {path}")
            prepared.append((row, path))

        reset = self.state.reset_record_stage(run_id)
        self.state.add_audit_event(
            "reprocess_records_started", run_id=run_id, actor="local-user",
            details={"responses": len(prepared), "reset": reset},
        )
        self._emit("before_reprocess", run_id=run_id, responses=len(prepared))
        processed = 0
        failures = 0
        for index, (row, path) in enumerate(prepared, 1):
            request = CrawlRequest(
                str(row["url"]),
                meta={"_fingerprint_override": str(row["request_fingerprint"]), "reprocessed": True},
            )
            result = FetchResult(
                request,
                str(row["final_url"]),
                int(row["status_code"]),
                {"content-type": str(row["content_type"] or "application/octet-stream")},
                path.read_bytes(),
                float(row["elapsed_seconds"] or 0),
            )
            try:
                self._handle_result(
                    run_id, result, 0, persist_response=False, discover=False
                )
                processed += 1
            except Exception as exc:
                failures += 1
                self.state.add_error(run_id, request, "reprocess", exc, retryable=True)
                self.diagnostics.failure(run_id, "reprocess", exc, request=request, result=result)
                self._emit("on_error", run_id=run_id, stage="reprocess", error=exc, request=request)
            if callback:
                callback(
                    "reprocess_progress",
                    {"processed": index, "total": len(prepared), "failures": failures},
                )
        exported = self._run_exports(run_id)
        self.metrics.record_stage("reprocess", time.monotonic() - reprocess_started)
        summary = {
            "run_id": run_id,
            "status": "succeeded",
            "has_errors": bool(failures),
            "reprocessed_responses": processed,
            "failures": failures,
            "reset": reset,
            **self.state.stats(run_id),
            "export": exported,
            "storage_warnings": list(self.record_sinks.errors),
        }
        self.state.add_audit_event(
            "reprocess_records_finished", run_id=run_id, actor="local-user", details=summary
        )
        self._emit("after_reprocess", run_id=run_id, summary=summary)
        if callback:
            callback("reprocess_completed", summary)
        return summary

    def _run_stream(self) -> dict[str, Any]:
        self.resource_guard.check(force=True)
        self.run_control.reset()
        self.egress.reconnect_task()
        run_id = self.state.start_run(self.config.project_name, str(self.config.path))
        total = 0
        status = "succeeded"
        try:
            self._emit("before_run", run_id=run_id, resume=False, retry_failed=False)
            for request in self.source.seed():
                if not self.run_control.wait_if_paused():
                    status = "cancelled"
                    self.egress.disconnect_task()
                    break
                self.resource_guard.check()
                if self._auth_provider is not None and self.config.source_kind != "long_poll":
                    request = prepare_request(self._auth_provider, request)
                if is_private_target(request.url) and not self.config.section("http").get("allow_private_network", False):
                    raise PermissionError("默认禁止访问本机、内网或保留地址")
                if self.config.source_kind in {"sse", "websocket"}:
                    policy_url = request.url.replace("wss://", "https://", 1).replace("ws://", "http://", 1)
                    allowed, reason = self.scope.allowed(policy_url, policy_url)
                    if not allowed:
                        raise PermissionError(reason)
                    if self.config.source_kind == "sse" and not self.robots.allowed(policy_url):
                        raise PermissionError("robots.txt不允许SSE地址，或robots检查失败且配置为fail-closed")
                if self.config.source_kind == "long_poll":
                    source = self.config.section("source")
                    maximum = int(source.get("max_messages", 100))
                    import time
                    started = time.monotonic()
                    duration = float(source.get("duration_seconds", 60))
                    collected = []
                    for _ in range(maximum):
                        if not self.run_control.wait_if_paused():
                            status = "cancelled"
                            self.egress.disconnect_task()
                            break
                        if time.monotonic() - started >= duration:
                            break
                        result = self._fetch_checked(run_id, request)
                        self.state.save_response(run_id, result, None)
                        processor_name = extractors.choose_processor(result)
                        if processor_name in self.registry.processors:
                            records = self._processor(processor_name).process(result).records
                            for transformer in self._transformers:
                                records = [transform_record(transformer, record) for record in records]
                            collected.extend(records)
                    total += self.state.save_records(run_id, request, collected)
                    self.record_sinks.write(run_id, request, collected)
                    self._emit(
                        "after_extract", run_id=run_id, result=None, records=collected,
                        count=len(collected), processor="stream",
                    )
                else:
                    self._emit("before_fetch", run_id=run_id, request=request)
                    should_continue = self.run_control.wait_if_paused
                    records = (
                        collect_websocket(self.config, request, should_continue, self.egress)
                        if self.config.source_kind == "websocket"
                        else collect_sse(self.config, request, should_continue, self.egress)
                    )
                    for transformer in self._transformers:
                        records = [transform_record(transformer, record) for record in records]
                    total += self.state.save_records(run_id, request, records)
                    self.record_sinks.write(run_id, request, records)
                    self._emit(
                        "after_extract", run_id=run_id, result=None, records=records,
                        count=len(records), processor="stream",
                    )
                    if self.run_control.read().get("stop_requested"):
                        status = "cancelled"
                        self.egress.disconnect_task()
                        break
            exported = self._run_exports(run_id)
            summary = {
                "run_id": run_id,
                "status": status,
                "messages": total,
                "resource_profile": profile_for(self.config).to_dict(),
                **self.state.stats(run_id),
                "export": exported,
            }
            summary["metrics"] = self.metrics.write(self.workspace / "output", self.workspace)
            summary["plugins"] = self.registry.describe()
            summary["storage_warnings"] = list(self.record_sinks.errors)
            self._emit("after_run", run_id=run_id, summary=summary)
            self._write_pipeline_summary(summary)
            self.state.finish_run(run_id, status, summary)
            return summary
        except KeyboardInterrupt:
            # E6：流式模式 Ctrl+C 也按取消处理并收尾，避免 run 卡在 running
            self.egress.disconnect_task()
            summary = {"run_id": run_id, "status": "cancelled", "messages": total}
            self._write_pipeline_summary(summary)
            self.state.finish_run(run_id, "cancelled", summary)
            raise
        except Exception as exc:
            self.state.add_error(run_id, None, "stream", exc, retryable=True)
            self._emit("on_error", run_id=run_id, stage="stream", error=exc, request=None)
            summary = {"run_id": run_id, "status": "failed", "messages": total, "error": str(exc)}
            self.state.finish_run(run_id, "failed", summary)
            raise
