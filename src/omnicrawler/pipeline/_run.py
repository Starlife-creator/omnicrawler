"""Run mixin: main crawl loop, reprocessing entry point and stream mode."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, as_completed, wait
from pathlib import Path
from typing import Any

from ..core.errors import ExtractionError, PolicyBlockedError, describe_error
from ..core.models import CrawlRequest, FetchResult
from ..extraction import extractors
from ..fetching.streams import collect_sse, collect_websocket
from ..pipeline_ops.pdf_integration import run_pdf_pipeline
from ..plugins.plugin_runtime import prepare_request, transform_record
from ..runtime.resource_profiles import effective_concurrency
from ..runtime.resources import ResourceLimitError
from ..security.policy import is_private_target
from ..services.progress import ProgressTracker, StageSpec, TaskProgressEvent
from ._mixin_base import _PipelineBase

LOGGER = logging.getLogger("omnicrawler")

# Pipeline 四阶段权重（归一化后即 10% / 40% / 30% / 20%）
_PIPELINE_STAGES = (
    StageSpec(name="ingest",  weight=1.0, display_name="种子入队（Ingest）", has_items=True),
    StageSpec(name="fetch",   weight=4.0, display_name="页面抓取（Fetch）",  has_items=True),
    StageSpec(name="extract", weight=3.0, display_name="结构化提取（Extract）", has_items=True),
    StageSpec(name="export",  weight=2.0, display_name="结果导出（Export）",  has_items=False),
)


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
        on_progress: Callable[[TaskProgressEvent], None] | None = None,
    ) -> dict[str, Any]:
        """Execute the full crawl pipeline and return the export summary.

        Args:
            resume: Continue from the last checkpoint instead of seeding fresh.
            retry_failed: Re-enqueue previously failed requests before crawling.
            max_pages: Override ``crawl.max_pages`` for this run.
            run_pdf: Force-enable or force-disable the PDF post-processing stage.
            callback: Optional legacy progress callback receiving ``(event, details)``.
            should_stop: Optional predicate; returning ``True`` cancels the run.
            on_progress: New unified progress event callback.  Each
                ``TaskProgressEvent`` covers stage weights (Ingest 10% / Fetch 40% /
                Extract 30% / Export 20%), EMA ETA and per-document item counts.

        Returns:
            The assembled run summary dict, including export results.
        """
        if self.config.source_kind in {"websocket", "sse", "long_poll"}:
            return self._run_stream(
                max_pages=max_pages,
                callback=callback,
                should_stop=should_stop,
                on_progress=on_progress,
            )
        if self.config.source_kind == "redis":
            raise RuntimeError("Redis分布式模式请使用 omnicrawler.redis_frontier.RedisFrontier；参见 docs/DISTRIBUTED.md")
        if self.config.source_kind == "scrapy":
            from ..sources.frameworks import run_scrapy
            return run_scrapy(self.config)

        # 进度协议初始化：task_id 先用 project_name，run_id 生成后再补
        tracker_task_id = ""
        try:
            tracker_task_id = str(self.config.project_name)
        except Exception:  # noqa: BLE001
            tracker_task_id = ""
        tracker = ProgressTracker(
            list(_PIPELINE_STAGES),
            task_id=tracker_task_id,
            on_event=on_progress,
        )

        # === Stage: Dispatch ===
        self.resource_guard.check(force=True)
        self.run_control.reset()
        self.egress.reconnect_task()
        run_id = self.state.start_run(self.config.project_name, str(self.config.path))
        tracker._task_id = run_id
        tracker.start()
        setup_started = time.monotonic()
        try:
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
                tracker.begin_stage("ingest")
                seeds_seen = 0
                for request in self.source.seed():
                    self.state.enqueue(request, force=True)
                    seeds_seen += 1
                    tracker.set_item_progress(seeds_seen, max(seeds_seen, 1))
                if seeds_seen:
                    tracker.set_item_progress(seeds_seen, seeds_seen)
                tracker.end_stage("ingest")
            else:
                tracker.end_stage("ingest")
            self.state.save_checkpoint(
                run_id,
                "setup",
                "setup",
                {"resume": resume, "retry_failed": retry_failed},
            )
            self.metrics.record_stage("setup", time.monotonic() - setup_started)
        except Exception as exc:
            tracker.fail(f"setup failed: {exc}")
            self.state.add_error(run_id, None, "setup", exc, retryable=False)
            self.diagnostics.failure(run_id, "setup", exc)
            self._emit("on_error", run_id=run_id, stage="setup", error=exc, request=None)
            summary = {"run_id": run_id, "status": "failed", "processed": 0, "error": str(exc), **self.state.stats(run_id)}
            self._write_pipeline_summary(summary)
            self.state.finish_run(run_id, "failed", summary)
            raise

        crawl = self.config.section("crawl")
        limit = int(crawl.get("max_pages", 100)) if max_pages is None else max_pages
        if limit < 0:
            raise ValueError(f"max_pages 不能为负数: {limit}")
        concurrency = effective_concurrency(self.config, int(crawl.get("concurrency", 4)))
        strategy = str(crawl.get("strategy", "bfs"))
        maximum_depth = int(crawl.get("max_depth", 3))
        attempts = int(self.config.section("http").get("retries", 3))
        max_requests = int(crawl.get("max_requests", limit * 5))
        if max_requests < 1:
            raise ValueError(f"max_requests 不能小于 1: {max_requests}")
        processed = 0
        attempted = 0
        started_monotonic = time.monotonic()
        status = "succeeded"
        pdf_summary: dict[str, Any] | None = None
        inflight: dict[Future[FetchResult], CrawlRequest] = {}
        frontier_exhausted = False

        tracker.begin_stage("fetch", expected_items=max(1, limit))
        extract_expected_items = max(1, limit)
        extract_processed = 0

        def consume(future: Future[FetchResult]) -> None:
            nonlocal processed, frontier_exhausted, extract_processed
            request = inflight.pop(future)
            try:
                result = future.result()
                self._handle_result(run_id, result, maximum_depth)
                frontier_exhausted = False
                self.state.mark_done(request.fingerprint)
                self.state.save_checkpoint(
                    run_id,
                    "fetch",
                    request.fingerprint,
                    {"url": result.final_url, "status": result.status},
                )
                processed += 1
                extract_processed += 1
                LOGGER.info("[%s/%s] %s %s", processed, limit, result.status, result.final_url)
                tracker.set_item_progress(processed, limit)
                if tracker._current_stage != "extract":
                    tracker.begin_stage("extract", expected_items=extract_expected_items)
                tracker.set_item_progress(extract_processed, extract_expected_items)
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
                self.metrics.increment("omnicrawler_failures_total", stage="policy", error=type(exc).__name__)
                self._emit("on_error", run_id=run_id, stage="policy", error=exc, request=request)
            except ExtractionError as exc:
                self.state.add_error(run_id, request, "extract", exc, retryable=False)
                self.state.mark_done(request.fingerprint, status="failed", error=str(exc))
                self.diagnostics.failure(run_id, "extract", exc, request=request)
                LOGGER.error("提取失败 %s: %s: %s", request.url, type(exc).__name__, exc)
                self.metrics.increment("omnicrawler_failures_total", stage="extract", error=type(exc).__name__)
                self._emit("on_error", run_id=run_id, stage="extract", error=exc, request=request)
            except Exception as exc:  # Per-URL isolation and retry boundary.
                info = describe_error(exc)
                self.state.add_error(run_id, request, "fetch", exc, retryable=info.retryable)
                self.state.mark_failed(request, exc, attempts, retryable=info.retryable)
                self.diagnostics.failure(run_id, "fetch", exc, request=request)
                LOGGER.error("抓取失败 %s: %s: %s", request.url, type(exc).__name__, exc)
                self.metrics.increment("omnicrawler_failures_total", stage="fetch", error=type(exc).__name__)
                self._emit("on_error", run_id=run_id, stage="fetch", error=exc, request=request)

        def drain() -> None:
            for future in as_completed(list(inflight)):
                consume(future)

        try:
            executor = self._get_executor(max(1, concurrency))

            def control_notify(event: str, details: dict[str, Any]) -> None:
                if event == "paused":
                    self.state.transition_run(run_id, "paused", reason="user_pause")
                    tracker.pause()
                elif event == "resumed":
                    self.state.transition_run(run_id, "running", reason="user_resume")
                    tracker.resume()
                if callback:
                    callback(event, details)

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
                    tracker.fail(f"resource exceeded: {exc}")
                    self.state.add_error(run_id, None, "resources", exc, retryable=True)
                    self.diagnostics.failure(run_id, "resources", exc)
                    self.metrics.increment(
                        "omnicrawler_failures_total", stage="resources", error=type(exc).__name__
                    )
                    self._emit("on_error", run_id=run_id, stage="resources", error=exc, request=None)
                    drain()
                    break
                if resource_snapshot.get("disk_free_bytes") is not None:
                    self.metrics.gauge(
                        "omnicrawler_disk_free_bytes", float(resource_snapshot["disk_free_bytes"])
                    )

                while (
                    not frontier_exhausted
                    and len(inflight) < concurrency
                    and processed + len(inflight) < limit
                    and attempted + len(inflight) < max_requests
                ):
                    want = min(
                        concurrency - len(inflight),
                        limit - processed - len(inflight),
                        max_requests - attempted - len(inflight),
                    )
                    batch = self.state.claim(want, strategy)
                    if not batch:
                        frontier_exhausted = True
                        break
                    for request in batch:
                        inflight[executor.submit(self._fetch_checked, run_id, request)] = request
                        attempted += 1

                if not inflight:
                    break

                wait_timeout = float(crawl.get("wait_timeout_seconds", 60))
                done, _pending = wait(inflight, return_when=FIRST_COMPLETED, timeout=wait_timeout)
                for future in done:
                    consume(future)

                self.metrics.gauge(
                    "omnicrawler_frontier_pending", float(self.state.pending_count())
                )
            try:
                tracker.end_stage("fetch")
            except Exception:  # noqa: BLE001
                pass
            try:
                tracker.end_stage("extract")
            except Exception:  # noqa: BLE001
                pass
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
                    "omnicrawler_pdf_documents", float(pdf_summary.get("documents", 0))
                )
                result_summary = pdf_summary.get("result", {})
                if isinstance(result_summary, dict):
                    for key in ("processed", "succeeded", "failed", "ocr_pages", "pages"):
                        value = result_summary.get(key)
                        if isinstance(value, (int, float)):
                            self.metrics.gauge(f"omnicrawler_pdf_{key}", float(value))
                self.state.save_checkpoint(run_id, "pdf", "pdf", pdf_summary)
                self.metrics.record_stage("pdf", time.monotonic() - pdf_started)

            # === Stage: Export ===
            tracker.begin_stage("export")
            exported = self._stage_exports(run_id, status, processed, pdf_summary, callback)
            tracker.end_stage("export")
            if status == "cancelled":
                tracker.cancel()
            elif status == "failed":
                tracker.fail()
            else:
                tracker.finish()
            return exported
        except KeyboardInterrupt:
            status = "cancelled"
            self.run_control.request_stop()
            self.egress.disconnect_task()
            drain()
            summary = {"run_id": run_id, "status": status, "processed": processed, **self.state.stats(run_id)}
            self._write_pipeline_summary(summary)
            self.state.finish_run(run_id, status, summary)
            tracker.cancel()
            raise
        except Exception as exc:
            status = "failed"
            self.egress.disconnect_task()
            drain()
            tracker.fail(f"pipeline failed: {exc}")
            self.state.add_error(run_id, None, "pipeline", exc, retryable=False)
            self.diagnostics.failure(run_id, "pipeline", exc)
            self._emit("on_error", run_id=run_id, stage="pipeline", error=exc, request=None)
            summary = {"run_id": run_id, "status": status, "processed": processed, "error": str(exc), **self.state.stats(run_id)}
            self._write_pipeline_summary(summary)
            self.state.finish_run(run_id, status, summary)
            raise
        finally:
            drain()

    def reprocess_records(
        self,
        run_id: str | None = None,
        *,
        callback: Callable[[str, dict[str, Any]], None] | None = None,
        on_progress: Callable[[TaskProgressEvent], None] | None = None,
    ) -> dict[str, Any]:
        """Re-run extraction, quality and export from archived responses without fetching."""

        # 只在有新回调时启用 progress tracker（不影响旧的 callback 语义）
        stages = (
            StageSpec(name="reprocess_extract", weight=7.0, display_name="重提取", has_items=True),
            StageSpec(name="reprocess_export",  weight=3.0, display_name="重导出", has_items=False),
        )
        tracker: ProgressTracker | None = None
        if on_progress is not None:
            tracker = ProgressTracker(list(stages), task_id=str(run_id or ""), on_event=on_progress)
            tracker.start()

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
        if tracker is not None:
            tracker.begin_stage("reprocess_extract", expected_items=max(1, len(prepared)))
        for index, (row, path) in enumerate(prepared, 1):
            try:
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
            except Exception as exc:
                failures += 1
                self.state.add_error(
                    run_id,
                    CrawlRequest(str(row["url"]), meta={"_fingerprint_override": str(row["request_fingerprint"])}),
                    "reprocess",
                    exc,
                    retryable=True,
                )
                self.diagnostics.failure(run_id, "reprocess", exc)
                self._emit("on_error", run_id=run_id, stage="reprocess", error=exc, request=None)
                if tracker is not None:
                    tracker.set_item_progress(index, len(prepared))
                if callback:
                    callback(
                        "reprocess_progress",
                        {"processed": index, "total": len(prepared), "failures": failures},
                    )
                continue
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
            if tracker is not None:
                tracker.set_item_progress(index, len(prepared))
            if callback:
                callback(
                    "reprocess_progress",
                    {"processed": index, "total": len(prepared), "failures": failures},
                )
        if tracker is not None:
            tracker.end_stage("reprocess_extract")
            tracker.begin_stage("reprocess_export")
        exported = self._run_exports(run_id, force=True)
        if tracker is not None:
            tracker.end_stage("reprocess_export")
            tracker.finish()
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

    def _run_stream(
        self,
        *,
        max_pages: int | None = None,
        callback: Callable[[str, dict[str, Any]], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
        on_progress: Callable[[TaskProgressEvent], None] | None = None,
    ) -> dict[str, Any]:
        self.resource_guard.check(force=True)
        self.run_control.reset()
        self.egress.reconnect_task()
        run_id = self.state.start_run(self.config.project_name, str(self.config.path))

        stages = (
            StageSpec(name="stream_ingest", weight=1.0, display_name="流式接收", has_items=True),
            StageSpec(name="stream_export", weight=1.0, display_name="流式导出", has_items=False),
        )
        tracker: ProgressTracker | None = None
        if on_progress is not None:
            tracker = ProgressTracker(list(stages), task_id=run_id, on_event=on_progress)
            tracker.start()
            tracker.begin_stage("stream_ingest")

        total = 0
        completed = 0
        status = "succeeded"
        try:
            self._emit("before_run", run_id=run_id, resume=False, retry_failed=False)
            for request in self.source.seed():
                if not self.run_control.wait_if_paused():
                    status = "cancelled"
                    self.egress.disconnect_task()
                    break
                if should_stop and should_stop():
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
                    started = time.monotonic()
                    duration = float(source.get("duration_seconds", 60))
                    stream_stopped = False
                    for _ in range(maximum):
                        if not self.run_control.wait_if_paused():
                            status = "cancelled"
                            self.egress.disconnect_task()
                            stream_stopped = True
                            break
                        if should_stop and should_stop():
                            status = "cancelled"
                            self.egress.disconnect_task()
                            stream_stopped = True
                            break
                        if time.monotonic() - started >= duration:
                            break
                        result = self._fetch_checked(run_id, request)
                        self.state.save_response(run_id, result, None)
                        processor_name = extractors.choose_processor(result)
                        collected = []
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
                        completed += 1
                        if tracker is not None:
                            tracker.set_item_progress(completed, max(1, maximum if max_pages is None else max_pages))
                        if callback:
                            callback(
                                "stream_progress",
                                {"processed": completed, "limit": max_pages, "messages": total},
                            )
                        if self.run_control.read().get("stop_requested"):
                            status = "cancelled"
                            self.egress.disconnect_task()
                            stream_stopped = True
                            break
                    if stream_stopped:
                        break
                else:
                    if self.config.source_kind == "sse":
                        stream_results = collect_sse(
                            self.config,
                            request,
                            should_continue=(lambda: not should_stop()) if should_stop else None,
                            egress=self.egress,
                            max_messages=max_pages,
                        )
                    elif self.config.source_kind == "websocket":
                        stream_results = collect_websocket(
                            self.config,
                            request,
                            should_continue=(lambda: not should_stop()) if should_stop else None,
                            egress=self.egress,
                            max_messages=max_pages,
                        )
                    else:
                        stream_results = []
                    for item in stream_results:
                        # collect_sse/collect_websocket 返回结构化 ExtractedRecord；
                        # 其 data 已是最终记录载荷，序列化为 JSON body 供 processor 处理。
                        result = FetchResult(
                            request, request.url, 200, {"content-type": "application/json"},
                            json.dumps(item.data, ensure_ascii=False).encode("utf-8"),
                            float(item.evidence.get("elapsed", 0) or 0),
                        )
                        result.meta.update(item.evidence)
                        processor_name = extractors.choose_processor(result)
                        collected = []
                        if processor_name in self.registry.processors:
                            records = self._processor(processor_name).process(result).records
                            for transformer in self._transformers:
                                records = [transform_record(transformer, record) for record in records]
                            collected.extend(records)
                        total += self.state.save_records(run_id, request, collected)
                        self.record_sinks.write(run_id, request, collected)
                        completed += 1
                        if tracker is not None:
                            tracker.set_item_progress(
                                completed, max(1, max_pages if max_pages is not None else completed)
                            )
                        if callback:
                            callback(
                                "stream_progress",
                                {"processed": completed, "limit": max_pages, "messages": total},
                            )
            if tracker is not None:
                tracker.end_stage("stream_ingest")
                tracker.begin_stage("stream_export")
            exported = self._stage_exports(run_id, status, completed, None, callback)
            if tracker is not None:
                tracker.end_stage("stream_export")
                if status == "cancelled":
                    tracker.cancel()
                else:
                    tracker.finish()
            self.state.finish_run(run_id, status, {"processed": completed, **self.state.stats(run_id)})
            return exported
        except KeyboardInterrupt:
            status = "cancelled"
            self.egress.disconnect_task()
            self.state.finish_run(run_id, status, {"processed": completed})
            if tracker is not None:
                tracker.cancel()
            raise
        except Exception as exc:
            status = "failed"
            self.egress.disconnect_task()
            self.diagnostics.failure(run_id, "stream", exc)
            self._emit("on_error", run_id=run_id, stage="stream", error=exc, request=None)
            summary = {"run_id": run_id, "status": status, "processed": completed, "error": str(exc), **self.state.stats(run_id)}
            self._write_pipeline_summary(summary)
            self.state.finish_run(run_id, status, summary)
            if tracker is not None:
                tracker.fail(str(exc))
            raise
