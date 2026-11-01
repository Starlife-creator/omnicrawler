"""超时回收必须唤醒 IPC 读线程，而不是只杀直接子进程。"""
from __future__ import annotations

import time
from pathlib import Path

from omnicrawler.plugins.plugin_sandbox import PluginSubprocessSession


def test_timeout_closes_streams_and_joins_reader(tmp_path: Path) -> None:
    (tmp_path / "slow.py").write_text(
        "def handle(operation, payload):\n    import time\n    time.sleep(30)\n    return {}\n",
        encoding="utf-8",
    )
    session = PluginSubprocessSession(
        tmp_path, "slow", timeout_seconds=0.15, handshake_timeout_seconds=0, sandbox_escape=True
    )
    with session:
        started = time.monotonic()
        try:
            session.call("slow", {})
        except TimeoutError:
            pass
        else:
            raise AssertionError("slow plugin should time out")
        assert time.monotonic() - started < 3
        assert session._proc is None
    session._kill()  # 幂等关闭；不残留临时入口或下一次会话。


def test_kill_is_idempotent_without_process(tmp_path: Path) -> None:
    (tmp_path / "probe.py").write_text("def handle(operation, payload): return {}\n", encoding="utf-8")
    session = PluginSubprocessSession(tmp_path, "probe", sandbox_escape=True)
    session._kill()
    session.end()
