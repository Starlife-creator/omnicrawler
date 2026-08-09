"""S2.5.11：browser pool 超时后任务丢弃 + context 资源释放。"""

from __future__ import annotations

import queue
import threading
from pathlib import Path

import pytest

from omnicrawl.core.config import load_config
from omnicrawl.core.models import CrawlRequest
from omnicrawl.fetching.browser_fetcher import PlaywrightPool, _PoolTask


def _config(tmp_path: Path) -> Path:
    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: s2511, workspace: work}\n"
        "source: {kind: static_html, seeds: [https://example.org/]}\n",
        encoding="utf-8",
    )
    return config_path


class _FakeContext:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _pool(tmp_path: Path, monkeypatch) -> PlaywrightPool:
    pool = object.__new__(PlaywrightPool)
    pool.config = load_config(_config(tmp_path))
    pool._queues = [queue.Queue()]
    pool._lock = threading.Lock()
    pool._closed = False
    pool._counter = 0
    monkeypatch.setattr(pool, "_fetch_timeout", lambda: 0.05)
    return pool


def test_fetch_timeout_marks_task_discarded(tmp_path: Path, monkeypatch) -> None:
    pool = _pool(tmp_path, monkeypatch)
    with pytest.raises(TimeoutError):
        pool.fetch(CrawlRequest("https://example.org/"))
    task = pool._queues[0].get_nowait()
    assert task.discarded.is_set()


def test_discarded_task_is_not_rendered(tmp_path: Path, monkeypatch) -> None:
    pool = _pool(tmp_path, monkeypatch)
    rendered: list[str] = []
    monkeypatch.setattr(pool, "_render", lambda *_a, **_k: rendered.append("x") or None)
    task = _PoolTask(CrawlRequest("https://example.org/"), threading.Event())
    task.discarded.set()
    pool._handle_task(object(), {}, task)
    assert rendered == []
    assert task.error is not None
    assert "丢弃" in str(task.error)
    assert task.done.is_set()


def test_render_completed_then_discarded_releases_context(tmp_path: Path, monkeypatch) -> None:
    pool = _pool(tmp_path, monkeypatch)
    request = CrawlRequest("https://example.org/")
    key = pool._context_key(request)
    ctx = _FakeContext()
    contexts: dict = {key: ctx}
    task = _PoolTask(request, threading.Event())

    def _fake_render(_browser, _contexts, _request):
        # 渲染期间调用方超时标记丢弃
        task.discarded.set()
        return "result"

    monkeypatch.setattr(pool, "_render", _fake_render)
    pool._handle_task(object(), contexts, task)
    assert task.done.is_set()
    assert key not in contexts
    assert ctx.closed


def test_render_error_propagates_to_task(tmp_path: Path, monkeypatch) -> None:
    pool = _pool(tmp_path, monkeypatch)

    def _boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(pool, "_render", _boom)
    task = _PoolTask(CrawlRequest("https://example.org/"), threading.Event())
    pool._handle_task(object(), {}, task)
    assert isinstance(task.error, RuntimeError)
    assert task.done.is_set()
