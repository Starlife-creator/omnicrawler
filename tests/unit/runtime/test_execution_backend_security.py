"""S2.5.21：execution_backend 会话权限（auth token）与超时错误信息。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from omnicrawler.runtime.execution_backend import WorkerSession, _write_session


def test_session_file_roundtrips_auth_token(tmp_path: Path) -> None:
    import os
    import stat

    path = tmp_path / "worker-session.json"
    session = WorkerSession(
        session_id="s2", config_path="task.yaml", workspace=str(tmp_path),
        address=r"\\.\pipe\omnicrawler-s2", family="AF_PIPE",
        auth_token="secret-token-abc", pid=1, status="starting", created_at="now",
    )
    _write_session(path, session)
    assert json.loads(path.read_text(encoding="utf-8"))["auth_token"] == "secret-token-abc"
    # IPC 安全核心是随机 auth_token（连接须 authkey 匹配）；POSIX 上会话
    # 文件尽力收紧为 0600（Windows 无 POSIX 权限语义，chmod 仅尽力而为）。
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


def test_startup_timeout_error_is_never_empty(tmp_path: Path, monkeypatch) -> None:

    from omnicrawler.runtime import execution_backend as module

    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: s2521, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        module.subprocess, "Popen", lambda *_a, **_k: SimpleNamespace(pid=99)
    )
    backend = module.LocalWorkerBackend()

    def _never_ready():
        raise EOFError("connection closed")

    monkeypatch.setattr(backend, "status", _never_ready)
    state = {"n": 0}

    def _clock():
        state["n"] += 1
        # 首次调用计算 deadline；此后立即越过时限 → 快速超时
        return 1_000_000_000.0 if state["n"] == 1 else 1_000_000_020.0

    monkeypatch.setattr(module.time, "monotonic", _clock)
    with pytest.raises(RuntimeError, match="Worker未在时限内就绪"):
        backend.start(config_path)


def test_startup_timeout_includes_last_error(tmp_path: Path, monkeypatch) -> None:

    from omnicrawler.runtime import execution_backend as module

    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: s2521b, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        module.subprocess, "Popen", lambda *_a, **_k: SimpleNamespace(pid=99)
    )
    backend = module.LocalWorkerBackend()

    def _never_ready():
        raise ConnectionError("socket unreachable")

    monkeypatch.setattr(backend, "status", _never_ready)
    state = {"n": 0}

    def _clock():
        state["n"] += 1
        if state["n"] == 1:
            return 2_000_000_000.0  # deadline 计算
        if state["n"] <= 4:
            return 2_000_000_005.0  # 循环内（未超时，记录 last_error）
        return 2_000_000_011.0  # 最终超时

    monkeypatch.setattr(module.time, "monotonic", _clock)
    with pytest.raises(RuntimeError, match="socket unreachable"):
        backend.start(config_path)
