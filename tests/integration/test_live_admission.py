import time
from types import SimpleNamespace

import pytest

from omnicrawler.services.quality_benchmark import TASKS, run_task


@pytest.mark.parametrize("engine", ["urllib", "httpx_async"])
def test_real_pipeline_uses_response_feedback_without_changing_delivery(tmp_path, monkeypatch, engine):
    from omnicrawler.fetching import async_fetcher, http_client
    from omnicrawler.pipeline import _run
    from omnicrawler.runtime.live_concurrency import LiveConcurrency
    from omnicrawler.services import quality_benchmark

    # Python 3.12 on Windows can complete fast requests inside one monotonic tick.
    # Freeze only the fetcher's coarse clock; retain the real short-duration clock.
    clock = SimpleNamespace(monotonic=lambda: 100.0, perf_counter=time.perf_counter, sleep=time.sleep)
    monkeypatch.setattr(http_client if engine == "urllib" else async_fetcher, "time", clock)
    task_config = quality_benchmark._task_config

    def configure(*args, **kwargs):
        config = task_config(*args, **kwargs)
        config["http"]["engine"] = engine
        return config

    monkeypatch.setattr(quality_benchmark, "_task_config", configure)
    observed = []
    original = LiveConcurrency.observe
    def observe(self, latency, **signals):
        observed.append(latency)
        return original(self, latency, **signals)
    monkeypatch.setattr(LiveConcurrency, "observe", observe)
    monkeypatch.setattr(_run, "local_resource_pressure", lambda _limit: False)
    score = run_task(TASKS[0], workdir=tmp_path / "actual")
    assert observed and any(value > 0 for value in observed)
    assert score.ok
