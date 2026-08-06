"""S2.5.36：run_finished 事件在异常路径也必发（finally 兜底）。"""

from __future__ import annotations

from pathlib import Path

from omnicrawl.services.application_service import ApplicationService


def test_run_finished_emitted_on_exception(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"project: {{name: ev, workspace: '{tmp_path / 'work'}'}}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    events: list[tuple[str, str]] = []
    service = ApplicationService(
        config_path, event_sink=lambda event: events.append((event["category"], event["name"]))
    )

    import omnicrawl.services.application_service as module

    class _BoomPipeline:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def run(self, **_kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(module, "Pipeline", _BoomPipeline)
    try:
        service.run()
    except RuntimeError:
        pass
    assert ("stage", "run_started") in events
    assert ("stage", "run_finished") in events


def test_run_finished_emitted_on_success(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        f"project: {{name: ev2, workspace: '{tmp_path / 'work'}'}}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    events: list[tuple[str, str]] = []
    service = ApplicationService(
        config_path, event_sink=lambda event: events.append((event["category"], event["name"]))
    )

    import omnicrawl.services.application_service as module

    class _OkPipeline:
        def __init__(self, _config):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def run(self, **_kwargs):
            return {"status": "succeeded"}

    monkeypatch.setattr(module, "Pipeline", _OkPipeline)
    result = service.run()
    assert result["status"] == "succeeded"
    assert ("stage", "run_finished") in events
