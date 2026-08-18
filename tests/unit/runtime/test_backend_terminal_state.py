"""S2.5.47：InProcessBackend/WorkerRuntime 非 dict 返回置终态。"""

from __future__ import annotations

import threading
from pathlib import Path

from omnicrawler.runtime.execution_backend import InProcessBackend
from omnicrawler.runtime.worker_main import WorkerRuntime


def test_inprocess_backend_non_dict_result_reaches_terminal_state(tmp_path: Path, monkeypatch) -> None:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: s2547, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    backend = InProcessBackend()

    class _FakeService:
        def run(self, callback=None):
            return "plain-value"

    monkeypatch.setattr(
        "omnicrawler.runtime.execution_backend.ApplicationService", lambda _p: _FakeService()
    )
    backend.start(config_path)
    deadline = 0
    while backend.status()["status"] == "running" and deadline < 100:
        threading.Event().wait(0.02)
        deadline += 1
    status = backend.status()
    assert status["status"] == "succeeded"
    assert status["result"]["value"] == "plain-value"


def test_worker_runtime_non_dict_result_does_not_crash(tmp_path: Path) -> None:
    from types import SimpleNamespace

    session = SimpleNamespace(config_path=str(tmp_path / "task.yaml"))
    runtime = WorkerRuntime.__new__(WorkerRuntime)
    runtime.session = session
    runtime.state = {"status": "running"}
    runtime._lock = threading.Lock()

    class _FakeService:
        def run(self, callback=None):
            return 42

    runtime.service = _FakeService()
    runtime._execute()
    assert runtime.state["status"] == "succeeded"
    assert runtime.state["result"]["value"] == 42
