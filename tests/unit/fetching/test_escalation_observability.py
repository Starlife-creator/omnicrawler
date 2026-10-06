from __future__ import annotations

from types import SimpleNamespace

import pytest

from omnicrawler.core.config import AppConfig
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.pipeline._fetch import _PipelineFetch
from omnicrawler.services.metrics import RunMetrics
from omnicrawler.state import StateStore


@pytest.fixture
def state(tmp_path):
    with StateStore(tmp_path / "state.sqlite") as store:
        yield store


@pytest.mark.parametrize("enabled", [True, False])
def test_production_escalation_preserves_reason_and_http_cost(tmp_path, enabled, state):
    pipeline = _PipelineFetch()
    pipeline.config = AppConfig(tmp_path / "c.yaml", tmp_path, {
        "http": {"auto_browser_fallback": enabled},
    }, tmp_path)
    pipeline._auth_provider = None
    pipeline._emit = lambda *_args, **_kwargs: []
    pipeline.scope = SimpleNamespace(allowed=lambda *_args: (True, ""))
    pipeline.robots = SimpleNamespace(allowed=lambda *_args: True)
    pipeline.metrics = RunMetrics()
    pipeline.state = state
    run_id = state.start_run("test", str(tmp_path / "c.yaml"))
    calls = []
    body = b'<div id="root"></div><script></script><script></script><script></script>'

    def fetch(name, request):
        calls.append(name)
        return FetchResult(request, request.url, 200, {"content-type": "text/html"},
                           body if name == "http" else b"<p>Delivered record</p>",
                           0.25 if name == "http" else 0.75)

    pipeline._thread_fetcher = lambda name: SimpleNamespace(fetch=lambda request: fetch(name, request))
    result = pipeline._fetch_checked(run_id, CrawlRequest("https://example.test/"))
    assert calls == (["http", "browser"] if enabled else ["http"])
    snapshot = pipeline.metrics.snapshot()
    decisions = [row for row in snapshot["counters"] if row["name"] == "omnicrawler_browser_decisions_total"]
    assert decisions == [{"name": "omnicrawler_browser_decisions_total", "labels": {
        "action": "upgrade" if enabled else "keep_http", "reason": "javascript_shell",
    }, "value": 1}]
    if enabled:
        assert result.meta["escalation_reason_code"] == "javascript_shell"
        assert result.meta["http_before_browser_bytes"] == len(body)
        assert snapshot["stage_durations"]["http_before_browser"]["average_seconds"] == 0.25
    else:
        assert "escalation_reason_code" not in result.meta
        assert "http_before_browser" not in snapshot["stage_durations"]
