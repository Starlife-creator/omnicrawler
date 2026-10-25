"""C-#7：``commands.plan`` 编译 / 对比分发与导出前脱敏拒写明文凭据直接单测。"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicrawler.commands import plan as plan_command


class _FakeService:
    last_compare: str | None = None

    def __init__(self, config_path: str) -> None:
        self.config_path = config_path

    def compile(self) -> dict[str, object]:
        return {
            "title": "hello",
            "secret": "abc123",
            "nested": {"password": "hunter2", "ok": True},
        }

    def diff(self, compare: str) -> dict[str, object]:
        type(self).last_compare = compare
        return {"diff": True}


def test_execute_compiles_without_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_command, "ApplicationService", _FakeService)
    result = plan_command.execute("project.yaml")
    assert result["title"] == "hello"
    assert "output" not in result


def test_execute_routes_to_diff_when_compare_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_command, "ApplicationService", _FakeService)
    result = plan_command.execute("project.yaml", compare="before")
    assert result == {"diff": True}
    assert _FakeService.last_compare == "before"


def test_export_redacts_sensitive_keys_before_writing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan_command, "ApplicationService", _FakeService)
    target = tmp_path / "plan.json"
    result = plan_command.execute("project.yaml", output=str(target))
    assert Path(result["output"]) == target
    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["title"] == "hello"
    assert written["secret"] == "<redacted>"
    assert written["nested"]["password"] == "<redacted>"
    assert written["nested"]["ok"] is True


def test_export_rejects_surviving_plaintext_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _SecretService(_FakeService):
        def compile(self) -> dict[str, object]:
            return {"note": "password: hunter2"}

    monkeypatch.setattr(plan_command, "ApplicationService", _SecretService)
    with pytest.raises(ValueError, match="明文凭据"):
        plan_command.execute("project.yaml", output=str(tmp_path / "plan.json"))
