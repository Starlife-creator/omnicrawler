from __future__ import annotations

import json
import subprocess
import threading
from types import SimpleNamespace

from omnicrawler.runtime.execution_backend import LocalWorkerBackend, WorkerSession, _read_session
from omnicrawler.runtime.worker_main import WorkerRuntime


def _session(tmp_path):
    return WorkerSession("test", str(tmp_path / "task.yaml"), str(tmp_path), "unused", "AF_PIPE",
                         "test-authentication", 42, "cancelled", "now")


def test_stop_acknowledgement_never_grants_session_edit_until_process_exits(tmp_path):
    backend = LocalWorkerBackend()
    backend.session = _session(tmp_path)
    backend.session_file = tmp_path / "session.json"
    status = {"status": "running"}
    commands = []
    backend._request = lambda command: commands.append(command) or dict(status)
    waits = []
    process = SimpleNamespace(returncode=None)

    def wait(timeout):
        waits.append(timeout)
        if process.returncode is None:
            raise subprocess.TimeoutExpired("worker", timeout)

    process.wait = wait
    backend._process = process
    assert not backend.close_for_session_edit()
    assert commands == ["status"]
    status["status"] = "cancelled"
    assert not backend.close_for_session_edit()
    assert commands == ["status", "status", "shutdown"]
    assert waits == [0]
    process.returncode = 0
    assert backend.close_for_session_edit()
    assert backend.session is None
    assert commands == ["status", "status", "shutdown"]
    assert backend.close_for_session_edit()


def test_worker_start_persists_resume_intent_and_old_session_defaults_false(tmp_path, monkeypatch):
    path = tmp_path / "task.yaml"
    path.write_text("project: {name: worker, workspace: work}\nsource: {kind: static_html, seeds: [https://example.test/]}\n", encoding="utf-8")
    backend = LocalWorkerBackend(worker_command=["unused"])
    monkeypatch.setattr("omnicrawler.runtime.execution_backend.subprocess.Popen", lambda *_args, **_kwargs: SimpleNamespace(pid=42))
    monkeypatch.setattr(backend, "status", lambda: {"status": "running"})
    backend.start(path, resume=True)
    assert backend.session_file is not None
    assert _read_session(backend.session_file).resume_from_checkpoint
    legacy = json.loads(backend.session_file.read_text(encoding="utf-8"))
    legacy.pop("resume_from_checkpoint")
    backend.session_file.write_text(json.dumps(legacy), encoding="utf-8")
    assert not _read_session(backend.session_file).resume_from_checkpoint


def test_worker_runtime_passes_resume_to_application_service(tmp_path):
    runtime = WorkerRuntime.__new__(WorkerRuntime)
    runtime.session = SimpleNamespace(resume_from_checkpoint=True)
    runtime._lock = threading.Lock()
    calls = []
    runtime.service = SimpleNamespace(run=lambda **kwargs: calls.append(kwargs) or {"status": "succeeded"})
    runtime._execute()
    assert calls and calls[0]["resume"] is True
    assert runtime.state["status"] == "succeeded"
