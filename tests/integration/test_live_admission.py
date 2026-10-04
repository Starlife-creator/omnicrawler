from omnicrawler.services.quality_benchmark import TASKS, run_task


def test_real_pipeline_uses_response_feedback_without_changing_delivery(tmp_path, monkeypatch):
    from omnicrawler.pipeline import _run
    from omnicrawler.runtime.live_concurrency import LiveConcurrency
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
