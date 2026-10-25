"""A-P3-3：异步抓取器关闭路径可观测性与线程安全回归测试。

- 关闭期单个客户端 / 事件循环异常记入 ``close_diagnostics`` 并写诊断日志，
  不阻断其余资源清理；
- ``close()`` 幂等，重复调用安全返回；
- 多线程并发关闭不损坏内部表。
"""
from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from types import SimpleNamespace

import yaml

from omnicrawler.core.config import load_config
from omnicrawler.fetching.async_fetcher import HTTPXAsyncFetcher


def _config(tmp_path: Path) -> object:
    workspace = tmp_path / "work"
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump({
        "project": {"name": "close", "workspace": str(workspace)},
        "source": {"kind": "crawl", "seeds": ["http://127.0.0.1:1/x"]},
        "crawl": {"max_pages": 1, "max_depth": 0, "same_host": True, "concurrency": 2},
        "http": {"user_agent": "CloseTest/1.0", "respect_robots": False, "delay_seconds": 0},
    }, sort_keys=False), encoding="utf-8")
    return load_config(path)


def _fetcher(tmp_path: Path) -> HTTPXAsyncFetcher:
    return HTTPXAsyncFetcher(
        _config(tmp_path),
        limiter=SimpleNamespace(wait=lambda _url: None),
        egress=SimpleNamespace(),
    )


class _FailingClient:
    async def aclose(self) -> None:
        raise RuntimeError("client close boom")


class _SyncLoop:
    """能驱动协程的同步假循环（用于触发 client.aclose 内部异常路径）。"""

    def is_closed(self) -> bool:
        return False

    def run_until_complete(self, coro: object) -> None:
        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(coro)  # type: ignore[arg-type]
        finally:
            loop.close()

    def close(self) -> None:
        pass


class _RaisingLoop:
    def __init__(self, *, loop_close_fails: bool = False) -> None:
        self._closed = False
        self._loop_close_fails = loop_close_fails

    def is_closed(self) -> bool:
        return self._closed

    def run_until_complete(self, coro: object) -> None:
        # 协程未被 await：显式关闭避免告警；模拟循环正在运行无法 run_until_complete。
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        raise RuntimeError("loop busy")

    def close(self) -> None:
        if self._loop_close_fails:
            raise RuntimeError("loop close boom")
        self._closed = True


def test_close_records_client_aclose_failure_and_clears_resources(tmp_path: Path) -> None:
    fetcher = _fetcher(tmp_path)
    loop = _SyncLoop()
    fetcher._loop_clients = {id(loop): (loop, _FailingClient())}

    fetcher.close()

    client_diag = [d for d in fetcher.close_diagnostics if d["stage"] == "client.aclose"]
    assert len(client_diag) == 1
    assert "client close boom" in client_diag[0]["error"]
    assert fetcher._loop_clients == {}
    assert fetcher._client is None


def test_close_records_loop_close_failure_and_resets_loop(tmp_path: Path) -> None:
    fetcher = _fetcher(tmp_path)
    loop = _RaisingLoop(loop_close_fails=True)
    fetcher._loop = loop

    fetcher.close()

    loop_diag = [d for d in fetcher.close_diagnostics if d["stage"] == "loop.close"]
    assert len(loop_diag) == 1
    assert "loop close boom" in loop_diag[0]["error"]
    assert fetcher._loop is None


def test_close_is_idempotent(tmp_path: Path) -> None:
    fetcher = _fetcher(tmp_path)
    fetcher.close()
    fetcher.close()
    assert fetcher._closed is True
    assert fetcher._loop_clients == {}
    assert fetcher._client is None
    assert fetcher._loop is None


def test_concurrent_close_does_not_corrupt_state(tmp_path: Path) -> None:
    fetcher = _fetcher(tmp_path)
    errors: list[BaseException] = []

    def _close() -> None:
        try:
            fetcher.close()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_close) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert fetcher._closed is True
    assert fetcher._loop_clients == {}
