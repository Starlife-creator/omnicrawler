from __future__ import annotations

from pathlib import Path

from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest
from omnicrawler.runtime.recovery import RecoveryCenter
from omnicrawler.state import StateStore


def _task(tmp_path: Path, name: str = "recovery") -> Path:
    path = tmp_path / "task.yaml"
    path.write_text(
        f"project: {{name: {name}, workspace: '{tmp_path / 'work'}'}}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    return path


def test_recovery_center_continue_retry_and_relogin_are_recoverable(tmp_path: Path) -> None:
    config = load_config(_task(tmp_path))
    config.workspace.mkdir(parents=True)
    with StateStore(config.workspace / "state.sqlite3") as state:
        run_id = state.start_run("recovery", str(config.path))
        request = CrawlRequest("https://example.org/fail")
        state.enqueue(request)
        state.claim(1)
        state.mark_failed(request, RuntimeError("boom"), max_attempts=1)
    sessions = config.workspace / "sessions"
    sessions.mkdir()
    (sessions / "default.cookies").write_text("private", encoding="utf-8")

    center = RecoveryCenter(config)
    overview = center.overview()
    assert overview["recommended_action"] == "continue"
    assert overview["action_previews"]["continue"]["affected"]["frontier_requests"] == 0
    assert overview["action_previews"]["retry-failed"]["affected"]["failed_requests"] == 1
    assert overview["action_previews"]["relogin"]["affected"]["session_files"] == 1
    assert center.continue_incomplete()["recovered_runs"] == [run_id]
    assert center.retry_failed()["retried"] == 1
    reset = center.reset_login()
    assert reset["moved"] == 1
    assert Path(reset["quarantine"]).joinpath("default.cookies").is_file()
    assert center.overview()["actions"] == [
        "continue", "retry-failed", "relogin", "reprocess", "rollback-config"
    ]


def test_recovery_center_config_rollback_preserves_current_file(tmp_path: Path) -> None:
    current = _task(tmp_path, "current")
    backup = tmp_path / "backup.yaml"
    backup.write_text(
        "project: {name: restored, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    result = RecoveryCenter(load_config(current)).rollback_config(backup)
    assert load_config(current).project_name == "restored"
    assert "name: current" in Path(result["previous_config"]).read_text(encoding="utf-8")


def test_s2520_reset_login_twice_in_same_second_succeeds(tmp_path: Path) -> None:
    from unittest import mock

    config = load_config(_task(tmp_path, "twice"))
    sessions = config.workspace / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "default.cookies").write_text("private", encoding="utf-8")

    center = RecoveryCenter(config)
    with mock.patch("omnicrawler.runtime.recovery.utcnow", return_value="2026-01-01T00:00:00+00:00"):
        first = center.reset_login()
        (sessions / "default.cookies").write_text("private-again", encoding="utf-8")
        second = center.reset_login()
    assert first["moved"] == 1 and second["moved"] == 1
    assert Path(first["quarantine"]) != Path(second["quarantine"])
