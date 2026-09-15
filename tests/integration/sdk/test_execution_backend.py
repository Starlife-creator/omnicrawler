from __future__ import annotations

import contextlib
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from omnicrawler.runtime.execution_backend import (
    UNIX_SOCKET_PATH_BUDGET,
    ExecutionBackend,
    FutureRemoteBackend,
    InProcessBackend,
    LocalWorkerBackend,
)


def _config(root: Path) -> Path:
    """在 `root` 下写一份 worker 配置；工作区取 `root/workspace`（可传深目录以复现长路径）。"""
    root.mkdir(parents=True, exist_ok=True)
    path = root / "worker.yaml"
    path.write_text(
        f"project: {{name: worker, workspace: '{root / 'workspace'}'}}\n"
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


def test_local_worker_is_authenticated_detached_and_reconnectable(tmp_path: Path) -> None:
    first = LocalWorkerBackend()
    started = first.start(_config(tmp_path))
    assert "status" in started
    assert first.session_file is not None and first.session_file.is_file()
    session_text = first.session_file.read_text(encoding="utf-8")
    assert "auth_token" in session_text
    # 家族按平台判定（旧断言写死 AF_PIPE，在 POSIX 上本就与实现不符）
    expected_family = "AF_PIPE" if os.name == "nt" else "AF_UNIX"
    assert expected_family in session_text, session_text[:200]

    reconnected = LocalWorkerBackend()
    status = reconnected.attach(first.session_file)
    assert "status" in status
    assert reconnected.pause()["paused"] is True
    assert reconnected.resume()["paused"] is False
    assert reconnected.shutdown()["shutdown"] is True


@pytest.mark.skipif(os.name == "nt", reason="AF_UNIX 路径长度是 POSIX 概念；Windows 走命名管道不适用")
def test_local_worker_starts_from_a_deep_workspace(tmp_path: Path) -> None:
    """**CI 失败形状**：工作区很深时 worker 仍必须起得来（W1.1，2026-09-15）。

    实测来源：CI 的 pytest 临时目录、以及用户把项目放在深层目录 ⇒ 旧实现把套接字放在工作区里，
    超出 POSIX `sun_path` 上限（macOS 104 / Linux 108 字节），worker 报
    `RuntimeError: 本地Worker启动超时: AF_UNIX path too long`——**不是测试问题，是产品缺陷**。
    """
    deep_root = tmp_path / ("d" * 60) / ("e" * 60) / "proj"
    backend = LocalWorkerBackend()
    try:
        started = backend.start(_config(deep_root))
        # ① 握手真的成功了（否则 start() 会抛「本地Worker启动超时」）
        assert "status" in started, started
        assert backend.session is not None
        address = Path(backend.session.address)
        # ② 套接字**不在工作区里**，且长度在预算内
        assert len(backend.session.address.encode("utf-8")) <= UNIX_SOCKET_PATH_BUDGET
        assert deep_root not in address.parents, address
        assert backend.pause()["paused"] is True
        assert backend.resume()["paused"] is False
    finally:
        with contextlib.suppress(Exception):
            backend.shutdown()


def test_local_worker_is_reaped_after_shutdown(tmp_path: Path) -> None:
    """关闭后必须**回收** worker 子进程（W1.1b，2026-09-15）。

    为什么需要：worker 退出后若没人 `wait()`，在 POSIX 上留下**僵尸**，而
    `psutil.pid_exists()`（`os.kill(pid, 0)`）对僵尸**仍返回真** ⇒ 端到端用例会报
    「结束后 worker 子进程 … 未退出，存在资源残留」（实测 macOS CI 6 条；Windows 无僵尸概念，本地不复现）。
    所以判据是"关闭后 pid 立刻不存在"，而不是"等 30 秒看看"。
    """
    psutil = pytest.importorskip("psutil")

    backend = LocalWorkerBackend()
    backend.start(_config(tmp_path))
    assert backend.session is not None
    pid = backend.session.pid
    assert pid and psutil.pid_exists(pid)

    backend.shutdown()

    assert not psutil.pid_exists(pid), f"关闭后 worker 仍是活进程或僵尸：{pid}"
    # 非父进程（重连进来的后端）调用 reap 必须安全（什么都不做但也不抛）
    assert backend.reap() is True
