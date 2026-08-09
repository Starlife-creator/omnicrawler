from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawl.pipeline_ops.pipeline_stages import FunctionStage, StageContext, require_stage_order
from omnicrawl.runtime.repository import RunRepository, SQLiteRunRepository
from omnicrawl.services.application_service import ApplicationService
from omnicrawl.services.controllers import ResultController, RunController, TaskController


def test_repository_port_and_sqlite_adapter(tmp_path: Path) -> None:
    with SQLiteRunRepository(tmp_path / "state.sqlite3") as repository:
        assert isinstance(repository, RunRepository)
        assert repository.latest_run() is None
        assert repository.stats()["frontier"] == {}


def test_pipeline_stage_contract_enforces_deterministic_order() -> None:
    context = StageContext("run", "hash", {})
    stage = FunctionStage("plan", lambda value: {"run_id": value.run_id})
    assert stage.execute(context) == {"run_id": "run"}
    assert context.values["plan"]["run_id"] == "run"
    require_stage_order(["plan", "policy", "fetch", "archive", "parse", "export"])
    with pytest.raises(ValueError):
        require_stage_order(["fetch", "plan"])
    with pytest.raises(ValueError):
        FunctionStage("mystery", lambda _value: {}).execute(context)


def test_controllers_are_thin_public_application_boundaries(tmp_path: Path) -> None:
    config = tmp_path / "task.yaml"
    config.write_text(
        f"project: {{name: controllers, workspace: '{tmp_path / 'work'}'}}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    service = ApplicationService(config)
    task = TaskController(service)
    run = RunController(service)
    results = ResultController(service)
    assert task.load()["config"]["project_name"] == "controllers"
    assert len(task.compile()["plan_hash"]) == 64
    assert run.pause()["paused"] is True
    assert run.resume()["paused"] is False
    assert results.query()["run"]["status"] == "not_started"
