"""C-#7：其余命令层纯参数校验 / 解析助手直接单测（服务与存储层以替身隔离）。"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from omnicrawler.commands import components as components_command
from omnicrawler.commands import field as field_command
from omnicrawler.commands import schedule as schedule_command
from omnicrawler.commands import task as task_command
from omnicrawler.commands import template as template_command
from omnicrawler.commands import transform as transform_command
from omnicrawler.commands import workspace as workspace_command


# ---------- template._key_values ----------

def test_key_values_parses_name_equals_value() -> None:
    assert template_command._key_values(["title=价格", "limit=20"], "=") == {"title": "价格", "limit": "20"}


def test_key_values_rejects_missing_separator_and_empty_name() -> None:
    with pytest.raises(ValueError, match="NAME=VALUE"):
        template_command._key_values(["title"], "=")
    with pytest.raises(ValueError, match="参数名不能为空"):
        template_command._key_values(["=value"], "=")


# ---------- task.compile_request ----------

def test_compile_request_rejects_empty_text() -> None:
    with pytest.raises(ValueError, match="需求不能为空"):
        task_command.compile_request("   ")


def test_compile_request_returns_reviewable_draft_mapping() -> None:
    result = task_command.compile_request(
        "抓取 https://example.com/list 的标题和价格，输出 CSV",
        fallback_url="https://example.com/list",
    )
    assert result["request"]
    assert "task" in result
    assert "fields" in result["task"]
    assert "warnings" in result["task"]


# ---------- transform.execute 操作门 ----------

def test_transform_requires_at_least_one_operation() -> None:
    with pytest.raises(ValueError, match="至少一个操作"):
        transform_command.execute("in.csv", None)


def test_transform_write_requires_target_when_confirmed() -> None:
    with pytest.raises(ValueError, match="目标文件"):
        transform_command.execute(
            "in.csv", None, sorts=["price:desc"], confirm=True,
        )


# ---------- field.execute_api_discover ----------

def test_api_discover_rejects_non_list_payload(tmp_path: Path) -> None:
    payload = tmp_path / "capture.json"
    payload.write_text(json.dumps({"api_responses": {"not": "list"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="JSON list"):
        field_command.execute_api_discover(str(payload), str(tmp_path / "bundle"))


def test_api_discover_delegates_to_bundle_writer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    payload = tmp_path / "capture.json"
    payload.write_text(json.dumps([{"url": "https://example.com/api"}]), encoding="utf-8")
    writer = MagicMock(return_value={"bundle": "ok"})
    import omnicrawler.extraction.api_discovery as discovery

    monkeypatch.setattr(discovery, "write_discovery_bundle", writer)
    result = field_command.execute_api_discover(str(payload), str(tmp_path / "bundle"))
    assert result == {"bundle": "ok"}
    writer.assert_called_once()


# ---------- components.execute 参数校验 ----------

def test_components_validates_required_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = MagicMock()
    monkeypatch.setattr(components_command, "manager", lambda: fake)

    with pytest.raises(ValueError, match="--package"):
        components_command.execute("inspect")
    with pytest.raises(ValueError, match="--package"):
        components_command.execute("import")
    with pytest.raises(ValueError, match="--name"):
        components_command.execute("uninstall")
    with pytest.raises(ValueError, match="--name"):
        components_command.execute("rollback")
    with pytest.raises(ValueError, match="--package和--sha256"):
        components_command.execute("stage", package="pkg.zip")
    with pytest.raises(ValueError, match="未知组件操作"):
        components_command.execute("bogus")


# ---------- workspace.execute 参数校验 ----------

def test_workspace_import_requires_target_and_destination() -> None:
    with pytest.raises(ValueError, match="--target"):
        workspace_command.execute("", "import")


def test_workspace_requires_config_for_non_import_actions() -> None:
    with pytest.raises(ValueError, match="--config"):
        workspace_command.execute("", "health")


# ---------- schedule.execute 本地调度库 ----------

def test_schedule_list_empty_store(tmp_path: Path) -> None:
    result = schedule_command.execute("list", database=str(tmp_path / "schedules.sqlite3"))
    assert result["schedules"] == []


def test_schedule_unknown_action_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="未知调度操作"):
        schedule_command.execute("bogus", database=str(tmp_path / "schedules.sqlite3"))
