from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
import yaml

from omnicrawl.core.config import load_config
from omnicrawl.pipeline import Pipeline


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        pages = {
            "/page1": (
                b"<html><title>Page1</title><h1>First</h1>"
                b"<a href='/page2'>Next</a><a href='/page3'>Third</a></html>"
            ),
            "/page2": b"<html><title>Page2</title><h1>Second</h1></html>",
            "/page3": b"<html><title>Page3</title><h1>Third</h1></html>",
        }
        if self.path not in pages:
            self.send_error(404)
            return
        body = pages[self.path]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


def _make_config(tmp_path: Path, port: int) -> Any:
    config_path = tmp_path / "project.yaml"
    workspace = tmp_path / "work"
    config_path.write_text(
        yaml.safe_dump(
            {
                "project": {"name": "security-test", "workspace": str(workspace)},
                "source": {
                    "kind": "incremental",
                    "seeds": [f"http://127.0.0.1:{port}/page1"],
                },
                "crawl": {
                    "max_pages": 10,
                    "max_depth": 2,
                    "same_host": True,
                    "concurrency": 1,
                },
                "http": {
                    "user_agent": "SecurityTest/1.0 (+contact: test@example.org)",
                    "respect_robots": False,
                    "delay_seconds": 0,
                    "allow_private_network": True,
                    "retries": 1,
                },
                "extract": {
                    "mode": "html",
                    "fields": {
                        "title": {"selector": "title"},
                        "heading": {"selector": "h1"},
                    },
                },
                "outputs": {"jsonl": True, "csv": True, "xlsx": False},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return load_config(config_path)


@pytest.fixture
def http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def test_stage_exception_caught_and_other_urls_continue(
    tmp_path: Path, http_server: ThreadingHTTPServer
) -> None:
    """九阶段编排异常流转测试: 验证某个阶段抛出异常时 Pipeline 能正确捕获并标记，不影响其他 URL。"""
    pytest.importorskip("bs4")
    config = _make_config(tmp_path, http_server.server_port)
    with Pipeline(config) as pipeline:
        original = pipeline._handle_result

        def patched(run_id: str, result: Any, maximum_depth: int, **kw: Any) -> None:
            if "/page2" in result.final_url:
                raise RuntimeError("simulated extract stage failure")
            return original(run_id, result, maximum_depth, **kw)

        pipeline._handle_result = patched  # type: ignore[method-assign]
        summary = pipeline.run()

        # Run itself succeeds despite one URL's stage failure
        assert summary["status"] == "succeeded"
        assert summary["processed"] >= 2

        # Error is recorded with the correct type
        errors = pipeline.state.rows(
            "SELECT stage, error_type, url FROM errors WHERE run_id=?",
            (summary["run_id"],),
        )
        assert any(e["error_type"] == "RuntimeError" for e in errors)

        # Failed URL is marked "failed"; other URLs are "done"
        frontier = pipeline.state.rows("SELECT url, status FROM frontier", ())
        statuses = {row["url"]: row["status"] for row in frontier}
        assert any(v == "done" for v in statuses.values())
        assert any(v == "failed" for v in statuses.values())


def test_single_url_failure_isolated_from_run(
    tmp_path: Path, http_server: ThreadingHTTPServer
) -> None:
    """单 URL 异常隔离边界测试: 验证单个 URL 失败不会拖垮整轮 run。"""
    pytest.importorskip("bs4")
    config = _make_config(tmp_path, http_server.server_port)
    with Pipeline(config) as pipeline:
        original = pipeline._fetch_checked

        def patched(run_id: str, request: Any) -> Any:
            if "/page2" in request.url:
                raise ConnectionError("simulated network failure")
            return original(run_id, request)

        pipeline._fetch_checked = patched  # type: ignore[method-assign]
        summary = pipeline.run()

        # Run succeeds; other URLs are processed normally
        assert summary["status"] == "succeeded"
        assert summary["processed"] >= 2

        # The connection error is recorded
        errors = pipeline.state.rows(
            "SELECT stage, error_type FROM errors WHERE run_id=?",
            (summary["run_id"],),
        )
        assert any(e["error_type"] == "ConnectionError" for e in errors)

        # Frontier reflects isolation: failed URL is "failed", others "done"
        frontier = pipeline.state.rows("SELECT status, COUNT(*) as n FROM frontier GROUP BY status", ())
        by_status = {row["status"]: row["n"] for row in frontier}
        assert by_status.get("done", 0) >= 2
        assert by_status.get("failed", 0) >= 1


def test_run_level_exception_properly_closes_run(
    tmp_path: Path, http_server: ThreadingHTTPServer
) -> None:
    """run 级异常收尾测试: 验证 run 级致命异常时 Pipeline 正确结束 run 并保留恢复状态。"""
    pytest.importorskip("bs4")
    config = _make_config(tmp_path, http_server.server_port)
    with Pipeline(config) as pipeline:
        def failing_exports(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("export stage catastrophic failure")

        pipeline._stage_exports = failing_exports  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="export stage catastrophic failure"):
            pipeline.run()

        # Run is marked as "failed" in state
        latest = pipeline.state.latest_run()
        assert latest is not None
        assert latest["status"] == "failed"

        # Summary file is written with failure info
        summary_path = pipeline.workspace / "output" / "pipeline_summary.json"
        assert summary_path.exists()
        summary_data = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary_data["status"] == "failed"
        assert "error" in summary_data
        assert summary_data["processed"] >= 1

        # Frontier state is preserved for recovery
        frontier = pipeline.state.rows(
            "SELECT status, COUNT(*) as n FROM frontier GROUP BY status", ()
        )
        by_status = {row["status"]: row["n"] for row in frontier}
        assert by_status.get("done", 0) >= 1

        # Error is recorded at pipeline level
        errors = pipeline.state.rows(
            "SELECT stage, error_type FROM errors WHERE run_id=?",
            (latest["run_id"],),
        )
        assert any(e["stage"] == "pipeline" for e in errors)


def test_keyboard_interrupt_saves_state(
    tmp_path: Path, http_server: ThreadingHTTPServer
) -> None:
    """KeyboardInterrupt 处理测试: 验证 Ctrl+C 时任务状态正确保存。"""
    pytest.importorskip("bs4")
    config = _make_config(tmp_path, http_server.server_port)
    with Pipeline(config) as pipeline:
        original = pipeline._handle_result
        interrupted = [False]

        def patched(run_id: str, result: Any, maximum_depth: int, **kw: Any) -> None:
            if not interrupted[0] and "/page2" in result.final_url:
                interrupted[0] = True
                raise KeyboardInterrupt
            return original(run_id, result, maximum_depth, **kw)

        pipeline._handle_result = patched  # type: ignore[method-assign]
        with pytest.raises(KeyboardInterrupt):
            pipeline.run()

        # Run is marked as "cancelled"
        latest = pipeline.state.latest_run()
        assert latest is not None
        assert latest["status"] == "cancelled"

        # run_control has stop_requested set
        rc_state = pipeline.run_control.read()
        assert rc_state.get("stop_requested") is True

        # Summary is written with cancelled status
        summary_path = pipeline.workspace / "output" / "pipeline_summary.json"
        assert summary_path.exists()
        summary_data = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary_data["status"] == "cancelled"
        assert summary_data["processed"] >= 1

        # Egress is disconnected for this task
        assert pipeline.egress._task_disabled.is_set()

        # Frontier preserves recovery state: page2 is still "in_progress"
        frontier = pipeline.state.rows("SELECT url, status FROM frontier", ())
        statuses = {row["url"]: row["status"] for row in frontier}
        page2_status = next(
            (v for url, v in statuses.items() if "/page2" in url), None
        )
        assert page2_status == "in_progress"
