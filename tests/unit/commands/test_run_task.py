"""C-#7 / A-P2-3：``commands.run_task`` 占位符门禁、退出码与 0 记录提示直接单测。"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from omnicrawler.commands import run_task


def _write_config(tmp_path: Path, *, extra: str = "") -> Path:
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump({
        "project": {"name": "run-task", "workspace": str(tmp_path / "work")},
        "source": {"kind": "crawl", "seeds": ["http://127.0.0.1:1/x"]},
        "crawl": {"max_pages": 1, "max_depth": 0, "same_host": True},
        "http": {"user_agent": "RunTaskTest/1.0", "respect_robots": False, "delay_seconds": 0},
        **(yaml.safe_load(extra) if extra else {}),
    }, sort_keys=False), encoding="utf-8")
    return path


class _FakeService:
    last_kwargs: dict[str, object] = {}

    def __init__(self, _path: Path) -> None:
        pass

    def run(self, **kwargs: object) -> dict[str, object]:
        type(self).last_kwargs = kwargs
        return dict(type(self).result)

    result: dict[str, object] = {"status": "succeeded", "records": 2, "processed": 2, "errors": 0}


def test_unresolved_placeholders_scan_nested_dict_and_list() -> None:
    hits = run_task._unresolved_placeholders({
        "http": {"headers": {"X-Auth": "Bearer {{token}}"}},
        "source": {"params": [{"id": "{{id}}"}]},
        "clean": "ok",
    })
    assert len(hits) == 2
    assert any("http.headers.X-Auth" in hit for hit in hits)
    assert any("source.params[0].id" in hit for hit in hits)
    assert run_task._unresolved_placeholders({"a": "plain"}) == []


def test_zero_record_hint_blocked_only() -> None:
    hint = run_task._zero_record_hint({"frontier": {"blocked": 3}, "processed": 0, "records": 0, "errors": 0})
    assert "被安全策略或 robots 拦住" in hint
    assert "security-report" in hint


def test_zero_record_hint_processed_but_no_records() -> None:
    hint = run_task._zero_record_hint({"frontier": {}, "processed": 5, "records": 0, "errors": 0})
    assert "已成功处理 5 个页面" in hint
    assert "增量去重" in hint


def test_zero_record_hint_nothing_processed() -> None:
    hint = run_task._zero_record_hint({"frontier": {}, "processed": 0, "records": 0, "errors": 0})
    assert "请求根本没发出去" in hint


def test_zero_record_hint_appends_errors_tail() -> None:
    hint = run_task._zero_record_hint({"frontier": {}, "processed": 0, "records": 0, "errors": 2})
    assert "2 条错误" in hint


def test_placeholder_gate_fails_closed_without_running_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_config(tmp_path, extra='http: {headers: {X-Auth: "Bearer {{token}}"}}')
    called: list[object] = []
    monkeypatch.setattr(run_task, "ApplicationService", lambda p: called.append(p) or (_ for _ in ()).throw(AssertionError("不应启动服务")))

    result = run_task.execute(str(path), "run")
    assert result["status"] == "failed"
    assert result["exit_code"] == 1
    assert result["records"] == 0
    assert called == []


@pytest.mark.parametrize(
    "status,strict,records,expected_code",
    [
        ("succeeded", False, 2, 0),
        ("failed", False, 0, 1),
        ("cancelled", False, 0, 1),
        ("succeeded", True, 2, 0),
        ("succeeded", True, 0, 1),
    ],
)
def test_exit_code_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    status: str, strict: bool, records: int, expected_code: int,
) -> None:
    path = _write_config(tmp_path)
    _FakeService.result = {"status": status, "records": records, "processed": records, "errors": 0}
    monkeypatch.setattr(run_task, "ApplicationService", _FakeService)

    result = run_task.execute(str(path), "run", strict=strict)
    assert result["exit_code"] == expected_code


def test_resume_and_retry_failed_forwarded_to_service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_config(tmp_path)
    _FakeService.result = {"status": "succeeded", "records": 1, "processed": 1, "errors": 0}
    monkeypatch.setattr(run_task, "ApplicationService", _FakeService)

    run_task.execute(str(path), "resume", retry_failed=True)
    assert _FakeService.last_kwargs["resume"] is True
    assert _FakeService.last_kwargs["retry_failed"] is True
