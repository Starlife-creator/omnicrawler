from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import patch

import pytest

from omnicrawler.runtime.execution_backend import (
    ExecutionBackend,
    FutureRemoteBackend,
    InProcessBackend,
    LocalWorkerBackend,
)


def _config(tmp_path: Path) -> Path:
    path = tmp_path / "worker.yaml"
    path.write_text(
        f"project: {{name: worker, workspace: '{tmp_path / 'workspace'}'}}\n"
        "source: {kind: static_html, seeds: [https://127.0.0.1/]}\n"
        "http: {respect_robots: false, retries: 0, timeout_seconds: 0.1}\n",
        encoding="utf-8",
    )
    return path


def test_in_process_and_future_backend_contracts(tmp_path: Path) -> None:
    backend = InProcessBackend()
    assert isinstance(backend, ExecutionBackend)
    with patch("omnicrawler.application_service.ApplicationService.run", return_value={"status": "succeeded"}):
        backend.start(_config(tmp_path))
        deadline = time.monotonic() + 2
        while backend.status()["status"] == "running" and time.monotonic() < deadline:
            time.sleep(0.01)
    assert backend.status()["status"] == "succeeded"
    assert isinstance(FutureRemoteBackend(), ExecutionBackend)
    with pytest.raises(NotImplementedError):
        FutureRemoteBackend().status()


@pytest.fixture
def _worker_socket_path_ok(tmp_path: Path) -> None:
    """Windows AF_PIPE 无路径长度限制 → 直接运行；POSIX AF_UNIX 路径过长则跳过。

    LocalWorkerBackend 的 socket 地址 = workspace/.worker-<uuidhex>.sock，
    Linux sun_path 上限 108 字节；CI 长工作区路径下会超限。
    """
    import os
    import socket as socket_module
    import uuid

    if os.name == "nt":
        return
    if not hasattr(socket_module, "AF_UNIX"):
        pytest.skip("平台无 AF_UNIX 支持")
    workspace = tmp_path / "workspace"
    address = str(workspace / f".worker-{uuid.uuid4().hex}.sock")
    if len(address.encode("utf-8")) >= 104:
        pytest.skip("AF_UNIX socket 路径过长（CI 长工作区），跳过实时握手测试")


@pytest.mark.usefixtures("_worker_socket_path_ok")
def test_local_worker_is_authenticated_detached_and_reconnectable(tmp_path: Path) -> None:
    first = LocalWorkerBackend()
    started = first.start(_config(tmp_path))
    assert "status" in started
    assert first.session_file is not None and first.session_file.is_file()
    session_text = first.session_file.read_text(encoding="utf-8")
    assert "auth_token" in session_text and "AF_PIPE" in session_text

    reconnected = LocalWorkerBackend()
    status = reconnected.attach(first.session_file)
    assert "status" in status
    assert reconnected.pause()["paused"] is True
    assert reconnected.resume()["paused"] is False
    assert reconnected.shutdown()["shutdown"] is True
