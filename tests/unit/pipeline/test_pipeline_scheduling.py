from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

import yaml

from omnicrawl.core.config import load_config
from omnicrawl.core.models import CrawlRequest, FetchResult
from omnicrawl.pipeline import Pipeline


def _make_config(tmp_path: Path, *, concurrency: int, max_pages: int) -> Any:
    """Build a minimal, network-free config for exercising the crawl loop."""
    config_path = tmp_path / "project.yaml"
    workspace = tmp_path / "work"
    config_path.write_text(
        yaml.safe_dump(
            {
                "project": {"name": "schedule-test", "workspace": str(workspace)},
                "source": {"kind": "incremental", "seeds": ["http://127.0.0.1:1/seed"]},
                "crawl": {
                    "max_pages": max_pages,
                    "max_depth": 0,
                    "same_host": True,
                    "concurrency": concurrency,
                },
                "http": {
                    "user_agent": "ScheduleTest/1.0 (+contact: test@example.org)",
                    "respect_robots": False,
                    "delay_seconds": 0,
                    "allow_private_network": True,
                    "retries": 1,
                },
                "extract": {"mode": "html", "fields": {"title": {"selector": "title"}}},
                "outputs": {"jsonl": False, "csv": False, "xlsx": False},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return load_config(config_path)


def _fake_request(index: int) -> CrawlRequest:
    return CrawlRequest(url=f"http://127.0.0.1:1/page{index}", meta={"index": index})


def _fake_result(request: CrawlRequest) -> FetchResult:
    return FetchResult(
        request,
        request.url,
        200,
        {"content-type": "text/html; charset=utf-8"},
        b"<html><title>ok</title></html>",
        0.0,
    )


def _install_frontier(
    pipeline: Pipeline,
    total: int,
    *,
    fetch: Any = None,
) -> None:
    """Seed ``total`` fake requests and neutralise heavy stages.

    ``_handle_result``/``_stage_exports`` are stubbed so the test observes only
    the scheduling behaviour of the crawl loop, while the real StateStore
    frontier bookkeeping (claim/mark_done/mark_failed) still runs.
    """
    requests = [_fake_request(i) for i in range(total)]
    pipeline.source.seed = lambda: iter(requests)  # type: ignore[method-assign]
    pipeline._handle_result = (  # type: ignore[method-assign]
        lambda run_id, result, maximum_depth, **kw: None
    )
    pipeline._stage_exports = (  # type: ignore[method-assign]
        lambda run_id, status, processed, pdf_summary, callback: {
            "run_id": run_id,
            "status": status,
            "processed": processed,
        }
    )
    if fetch is not None:
        pipeline._fetch_checked = fetch  # type: ignore[method-assign]


def _frontier_by_status(pipeline: Pipeline) -> dict[str, int]:
    rows = pipeline.state.rows(
        "SELECT status, COUNT(*) AS n FROM frontier GROUP BY status", ()
    )
    return {row["status"]: row["n"] for row in rows}


def test_rolling_window_never_exceeds_concurrency_and_stops_at_limit(
    tmp_path: Path,
) -> None:
    """连续调度: 在途窗口不超上限, 且达到 limit 精确停止 (无超额 claim)。"""
    concurrency = 4
    limit = 8
    config = _make_config(tmp_path, concurrency=concurrency, max_pages=limit)

    active = 0
    max_active = 0
    calls = 0
    lock = threading.Lock()

    def fetch(run_id: str, request: CrawlRequest) -> FetchResult:
        nonlocal active, max_active, calls
        with lock:
            active += 1
            calls += 1
            max_active = max(max_active, active)
        time.sleep(0.02)
        with lock:
            active -= 1
        return _fake_result(request)

    with Pipeline(config) as pipeline:
        _install_frontier(pipeline, total=20, fetch=fetch)
        summary = pipeline.run()

        assert summary["status"] == "succeeded"
        # Exact limit stop: never fetch more than the cap even with 20 available.
        assert calls == limit
        # Rolling window utilisation: multiple requests overlap in flight ...
        assert max_active >= 2
        # ... but the window never exceeds the concurrency ceiling.
        assert max_active <= concurrency
        by_status = _frontier_by_status(pipeline)
        assert by_status.get("done", 0) == limit
        assert by_status.get("in_progress", 0) == 0


def test_frontier_exhaustion_exits_before_limit(tmp_path: Path) -> None:
    """frontier 提前耗尽: 少于 limit 的种子应在耗尽后正常退出。"""
    config = _make_config(tmp_path, concurrency=4, max_pages=100)
    calls = 0
    lock = threading.Lock()

    def fetch(run_id: str, request: CrawlRequest) -> FetchResult:
        nonlocal calls
        with lock:
            calls += 1
        return _fake_result(request)

    with Pipeline(config) as pipeline:
        _install_frontier(pipeline, total=5, fetch=fetch)
        summary = pipeline.run()

        assert summary["status"] == "succeeded"
        assert calls == 5
        by_status = _frontier_by_status(pipeline)
        assert by_status.get("done", 0) == 5
        assert by_status.get("in_progress", 0) == 0
        assert by_status.get("pending", 0) == 0


def test_per_url_failure_isolated_under_concurrency(tmp_path: Path) -> None:
    """逐 URL 失败隔离: 部分 URL 失败不影响其余在途请求处理。"""
    config = _make_config(tmp_path, concurrency=3, max_pages=100)

    def fetch(run_id: str, request: CrawlRequest) -> FetchResult:
        if request.meta.get("index", -1) % 3 == 0:
            raise ConnectionError("simulated failure")
        return _fake_result(request)

    with Pipeline(config) as pipeline:
        _install_frontier(pipeline, total=9, fetch=fetch)
        summary = pipeline.run()

        assert summary["status"] == "succeeded"
        by_status = _frontier_by_status(pipeline)
        # indices 0,3,6 fail (retries=1 -> permanent failed); the other 6 succeed.
        assert by_status.get("failed", 0) == 3
        assert by_status.get("done", 0) == 6
        assert by_status.get("in_progress", 0) == 0
        errors = pipeline.state.rows(
            "SELECT error_type FROM errors WHERE run_id=?", (summary["run_id"],)
        )
        assert any(e["error_type"] == "ConnectionError" for e in errors)


def test_should_stop_drains_inflight_without_orphans(tmp_path: Path) -> None:
    """停止路径: should_stop 触发后排空在途请求, 不留 in_progress 孤儿。"""
    concurrency = 4
    config = _make_config(tmp_path, concurrency=concurrency, max_pages=100)
    started = 0
    lock = threading.Lock()

    def fetch(run_id: str, request: CrawlRequest) -> FetchResult:
        nonlocal started
        with lock:
            started += 1
        time.sleep(0.02)
        return _fake_result(request)

    def should_stop() -> bool:
        with lock:
            return started >= 1

    with Pipeline(config) as pipeline:
        _install_frontier(pipeline, total=20, fetch=fetch)
        summary = pipeline.run(should_stop=should_stop)

        assert summary["status"] == "cancelled"
        assert pipeline.egress._task_disabled.is_set()
        by_status = _frontier_by_status(pipeline)
        # The window submitted in the first iteration is drained cleanly.
        assert by_status.get("in_progress", 0) == 0
        assert by_status.get("done", 0) <= concurrency
        # Remaining seeds stay pending for a later resume.
        assert by_status.get("pending", 0) >= 20 - concurrency


def test_pause_stop_signal_drains_inflight(tmp_path: Path) -> None:
    """暂停/停止信号: wait_if_paused 返回 False 时排空在途并标记取消。"""
    concurrency = 3
    config = _make_config(tmp_path, concurrency=concurrency, max_pages=100)
    calls = {"n": 0}

    def fetch(run_id: str, request: CrawlRequest) -> FetchResult:
        time.sleep(0.01)
        return _fake_result(request)

    with Pipeline(config) as pipeline:
        _install_frontier(pipeline, total=20, fetch=fetch)

        def wait_if_paused(*, notify: Any = None, poll_seconds: float = 0.25) -> bool:
            calls["n"] += 1
            # Allow the first scheduling iteration, then signal a stop.
            return calls["n"] < 2

        pipeline.run_control.wait_if_paused = wait_if_paused  # type: ignore[method-assign]
        summary = pipeline.run()

        assert summary["status"] == "cancelled"
        assert pipeline.egress._task_disabled.is_set()
        by_status = _frontier_by_status(pipeline)
        assert by_status.get("in_progress", 0) == 0
        assert by_status.get("done", 0) <= concurrency
