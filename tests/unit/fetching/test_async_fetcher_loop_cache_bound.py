"""A-P3-1：异步抓取器跨事件循环客户端表淘汰契约回归测试。

- 已关闭事件循环的残留条目被回收；
- 表超过容量后 FIFO 淘汰最旧条目，不再随循环数量无界增长。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from omnicrawler.core.config import load_config
from omnicrawler.fetching import async_fetcher as fetcher_module
from omnicrawler.fetching.async_fetcher import HTTPXAsyncFetcher


def _config(tmp_path: Path) -> object:
    workspace = tmp_path / "work"
    path = tmp_path / "project.yaml"
    path.write_text(yaml.safe_dump({
        "project": {"name": "loops", "workspace": str(workspace)},
        "source": {"kind": "crawl", "seeds": ["http://127.0.0.1:1/x"]},
        "crawl": {"max_pages": 1, "max_depth": 0, "same_host": True, "concurrency": 2},
        "http": {"user_agent": "LoopBoundTest/1.0", "respect_robots": False, "delay_seconds": 0},
    }, sort_keys=False), encoding="utf-8")
    return load_config(path)


def _fetcher(tmp_path: Path) -> HTTPXAsyncFetcher:
    return HTTPXAsyncFetcher(
        _config(tmp_path),
        limiter=SimpleNamespace(wait=lambda _url: None),
        egress=SimpleNamespace(),
    )


class _FakeLoop:
    def __init__(self, closed: bool = False) -> None:
        self._closed = closed

    def is_closed(self) -> bool:
        return self._closed


def test_closed_loop_entries_are_evicted(tmp_path: Path) -> None:
    fetcher = _fetcher(tmp_path)
    open_loop, closed_loop = _FakeLoop(), _FakeLoop(closed=True)
    fetcher._loop_clients = {
        id(open_loop): (open_loop, object()),
        id(closed_loop): (closed_loop, object()),
    }
    fetcher._evict_loop_clients()
    assert id(open_loop) in fetcher._loop_clients
    assert id(closed_loop) not in fetcher._loop_clients


def test_loop_client_table_is_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(fetcher_module, "_MAX_LOOP_CLIENTS", 2)
    fetcher = _fetcher(tmp_path)
    loops = [_FakeLoop() for _ in range(5)]
    for loop in loops:
        fetcher._loop_clients[id(loop)] = (loop, object())
    fetcher._evict_loop_clients()
    assert len(fetcher._loop_clients) == 2
    assert id(loops[0]) not in fetcher._loop_clients
    assert id(loops[4]) in fetcher._loop_clients
