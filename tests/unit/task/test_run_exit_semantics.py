from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from omnicrawler.commands import run_task
from omnicrawler.core.run_state import (
    ALLOWED_TRANSITIONS,
    RUN_STATES,
    TERMINAL_RUN_STATES,
    canonical_run_state,
    require_transition,
)


def test_partial_success_is_an_independent_terminal_state() -> None:
    assert "partial_success" in RUN_STATES
    assert "partial_success" in TERMINAL_RUN_STATES
    assert "partial_success" in ALLOWED_TRANSITIONS["running"]
    assert require_transition("running", "partial_success") == (
        "running", "partial_success",
    )
    with pytest.raises(ValueError, match="非法任务状态转换"):
        require_transition("partial_success", "running")


def test_completed_with_errors_no_longer_aliases_to_succeeded() -> None:
    assert canonical_run_state("completed_with_errors") == "partial_success"
    assert canonical_run_state("completed") == "succeeded"


class _FakeService:
    def __init__(self, result: dict) -> None:
        self._result = result

    def run(self, **kwargs: object) -> dict:
        return dict(self._result)


def _run_execute(tmp_path: Path, monkeypatch, result: dict, *, strict: bool = False) -> dict:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: s2, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    loaded = SimpleNamespace(path=config_path)
    monkeypatch.setattr(run_task, "load_config", lambda _p: loaded)
    monkeypatch.setattr(run_task, "ApplicationService", lambda _p: _FakeService(result))
    return run_task.execute(str(config_path), "run", strict=strict)


def _summary(**overrides: object) -> dict:
    payload: dict = {
        "status": "succeeded", "processed": 3, "records": 2, "artifacts": 0,
        "elapsed_seconds": 1.5, "errors": 0, "workspace": "",
    }
    payload.update(overrides)
    return payload


def test_exit_code_succeeded_with_records_is_zero(tmp_path: Path, monkeypatch) -> None:
    result = _run_execute(tmp_path, monkeypatch, _summary())
    assert result["exit_code"] == 0
    assert result["effective_records"] == 2


def test_exit_code_failed_or_cancelled_is_one(tmp_path: Path, monkeypatch) -> None:
    assert _run_execute(tmp_path, monkeypatch, _summary(status="failed"))["exit_code"] == 1
    assert _run_execute(tmp_path, monkeypatch, _summary(status="cancelled"))["exit_code"] == 1


def test_strict_zero_records_exits_one_and_prints_hint(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    result = _run_execute(
        tmp_path, monkeypatch, _summary(records=0), strict=True,
    )
    assert result["exit_code"] == 1
    assert result["effective_records"] == 0
    out = capsys.readouterr().out
    assert "有效记录为 0" in out
    assert "doctor" in out


def test_non_strict_zero_records_stays_compatible(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    result = _run_execute(tmp_path, monkeypatch, _summary(records=0), strict=False)
    assert result["exit_code"] == 0
    assert "有效记录为 0" in capsys.readouterr().out


def test_strict_partial_success_is_nonzero(tmp_path: Path, monkeypatch) -> None:
    result = _run_execute(
        tmp_path, monkeypatch, _summary(status="partial_success"), strict=True,
    )
    assert result["exit_code"] == 1


def test_non_strict_partial_success_is_zero(tmp_path: Path, monkeypatch) -> None:
    result = _run_execute(
        tmp_path, monkeypatch, _summary(status="partial_success"), strict=False,
    )
    assert result["exit_code"] == 0


def test_summary_always_carries_effective_records(tmp_path: Path, monkeypatch) -> None:
    result = _run_execute(tmp_path, monkeypatch, _summary())
    assert "effective_records" in result
