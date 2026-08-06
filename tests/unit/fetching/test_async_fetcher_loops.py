"""S1.5.8 消费方测试：async 抓取器跨事件循环。

验证：两个不同线程各自跑自己的 loop 调用 fetch_many 时，客户端按 loop 隔离
（不再强绑单一 _loop），不触发 "Future attached to a different loop"。
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from omnicrawl.core.config import load_config
from omnicrawl.fetching.async_fetcher import HTTPXAsyncFetcher


def _config(tmp_path: Path) -> object:
    workspace = tmp_path / "work"
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump({
        "project": {"name": "aloop", "workspace": str(workspace)},
        "source": {"kind": "crawl", "seeds": ["http://127.0.0.1:1/x"]},
        "crawl": {"max_pages": 1, "max_depth": 0, "same_host": True, "concurrency": 2},
        "http": {
            "user_agent": "AsyncLoopTest/1.0 (+contact: test@example.org)",
            "respect_robots": False, "delay_seconds": 0, "retries": 1, "allow_private_network": True,
            "timeout_seconds": 5, "max_response_bytes": 1_000_000,
        },
    }, sort_keys=False), encoding="utf-8")
    return load_config(path)


def _fetcher(monkeypatch, tmp_path: Path, clients: list[object]) -> HTTPXAsyncFetcher:
    fetcher = HTTPXAsyncFetcher(_config(tmp_path))
    monkeypatch.setattr(
        fetcher, "_build_client",
        lambda: type("FakeClient", (), {"__init__": lambda self: clients.append(self)})(),
    )
    monkeypatch.setattr(fetcher.limiter, "wait", lambda _url: None)
    return fetcher


def test_s158_fetch_many_binds_client_per_loop(monkeypatch, tmp_path: Path) -> None:
    """S1.5.8：每个 running loop 各自获得独立客户端，同 loop 复用。"""
    clients: list[object] = []
    fetcher = _fetcher(monkeypatch, tmp_path, clients)

    async def count_clients() -> int:
        await fetcher.fetch_many([])  # 空列表不触网，仅验证 loop 绑定
        return len(clients)

    def run_in(loop: asyncio.AbstractEventLoop) -> int:
        return loop.run_until_complete(count_clients())

    loop_a = asyncio.new_event_loop()
    loop_b = asyncio.new_event_loop()
    try:
        assert run_in(loop_a) == 1        # A loop 首次新建客户端
        assert run_in(loop_b) == 2        # B loop 新建独立客户端（不复用 A 的）
        assert run_in(loop_a) == 2        # 同 loop 复用，不再新建
        assert run_in(loop_b) == 2
        assert clients[0] is not clients[1], "不同 loop 必须持有不同客户端"
        assert fetcher.close is not None  # 占位避免未使用告警
    finally:
        loop_a.close()
        loop_b.close()
        fetcher.close()
        assert not fetcher._loop_clients, "close 后 per-loop 客户端缓存应清空"


def test_s158_threads_different_loops_run_in_parallel(monkeypatch, tmp_path: Path) -> None:
    """S1.5.8：两个线程各自独立 loop 并行 fetch_many，无跨 loop Future 错误。"""
    clients: list[object] = []
    fetcher = _fetcher(monkeypatch, tmp_path, clients)

    async def main() -> int:
        await fetcher.fetch_many([])
        return 0

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(lambda: asyncio.run(main())) for _ in range(2)]
            results = [future.result() for future in futures]
            assert results == [0, 0]  # 两个 loop 各自完成，无跨 loop 异常
    finally:
        fetcher.close()
