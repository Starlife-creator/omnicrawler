"""超时回收必须唤醒 IPC 读线程，而不是只杀直接子进程。"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from omnicrawler.plugins.plugin_sandbox import PluginSubprocessSession


def test_timeout_closes_streams_and_joins_reader(tmp_path: Path) -> None:
    (tmp_path / "slow.py").write_text(
        "def handle(operation, payload):\n    import time\n    time.sleep(30)\n    return {}\n",
        encoding="utf-8",
    )
    session = PluginSubprocessSession(
        tmp_path, "slow", timeout_seconds=0.15, handshake_timeout=0, sandbox_escape=True
    )
    with session:
        session._handshake_timeout = 0  # 本用例测请求超时，不测冻结宿主冷启动宽限。
        started = time.monotonic()
        with pytest.raises(TimeoutError, match="响应超时"):
            session.call("slow", {})
        assert time.monotonic() - started < 3
        assert session._proc is None
    session._kill()  # 幂等关闭；不残留临时入口或下一次会话。


def test_kill_is_idempotent_without_process(tmp_path: Path) -> None:
    (tmp_path / "probe.py").write_text("def handle(operation, payload): return {}\n", encoding="utf-8")
    session = PluginSubprocessSession(tmp_path, "probe", sandbox_escape=True)
    session._kill()
    session.end()
