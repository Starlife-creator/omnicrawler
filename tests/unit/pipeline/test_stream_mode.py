"""S2.5.4：流式模式参数透传（进度回调与 should_stop 取消）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.core.config import load_config
from omnicrawler.core.models import FetchResult


def _fetch_factory(calls: list[int]):
    def _fetch(run_id, request):
        calls.append(1)
        return FetchResult(
            request, request.url, 200,
            {"content-type": "text/event-stream"}, b"data: {}\n\n", 0.05,
        )
    return _fetch


def _long_poll_config(tmp_path: Path) -> Path:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"project: {{name: s254, workspace: {str(tmp_path / 'work').replace(chr(92), '/')}}}\n"
        "source: {kind: long_poll, seeds: [https://example.org/feed], max_messages: 5, duration_seconds: 60}\n"
        "http: {respect_robots: false, allow_private_network: true}\n",
        encoding="utf-8",
    )
    return config_path


def test_long_poll_partial_failure_keeps_collected_data(tmp_path: Path, monkeypatch) -> None:
    """S2.5.46：中途失败保留已收集消息（增量落库）。"""
    from omnicrawler.pipeline import Pipeline as PipelineCls

    calls: list[int] = []

    def _fetch(run_id, request):
        calls.append(1)
        if len(calls) >= 3:
            raise RuntimeError("connection dropped")
        return FetchResult(
            request, request.url, 200,
            {"content-type": "text/plain"}, b"chunk", 0.05,
        )

    with PipelineCls(load_config(_long_poll_config(tmp_path))) as pipeline:
        monkeypatch.setattr(pipeline, "_fetch_checked", _fetch)
        with pytest.raises(RuntimeError, match="connection dropped"):
            pipeline.run(callback=lambda _e, _d: None)
        rows = pipeline.state.rows("SELECT COUNT(*) AS n FROM responses")
        assert rows[0]["n"] == 2  # 前两条已增量落库


def test_stream_cancel_via_should_stop(tmp_path: Path, monkeypatch) -> None:
    from omnicrawler.pipeline import Pipeline as PipelineCls

    calls: list[int] = []
    with PipelineCls(load_config(_long_poll_config(tmp_path))) as pipeline:
        monkeypatch.setattr(pipeline, "_fetch_checked", _fetch_factory(calls))
        result = pipeline.run(
            callback=lambda _e, _d: None,
            should_stop=lambda: len(calls) >= 3,
        )
    assert result["status"] == "cancelled"
    assert len(calls) >= 3


def test_stream_progress_callback_receives_events(tmp_path: Path, monkeypatch) -> None:
    from omnicrawler.pipeline import Pipeline as PipelineCls

    events: list[dict] = []

    def _on_event(event: str, details: dict) -> None:
        if event == "stream_progress":
            events.append(details)

    with PipelineCls(load_config(_long_poll_config(tmp_path))) as pipeline:
        monkeypatch.setattr(pipeline, "_fetch_checked", _fetch_factory([]))
        result = pipeline.run(max_pages=3, callback=_on_event)
    assert result["status"] == "succeeded"
    assert [item["processed"] for item in events] == [1, 2, 3, 4, 5]
    assert all(isinstance(item["messages"], int) for item in events)
    assert all(item["limit"] == 3 for item in events)
