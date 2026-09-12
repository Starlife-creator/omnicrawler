"""S2.5.11：browser pool 超时后任务丢弃 + context 资源释放。"""

from __future__ import annotations

import queue
import threading
from pathlib import Path

import pytest

from omnicrawler.core.config import load_config
from omnicrawler.core.models import CrawlRequest
from omnicrawler.fetching.browser_fetcher import PlaywrightPool, _PoolTask


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


def test_playwright_launch_args_reject_tls_disable(tmp_path: Path, monkeypatch) -> None:
    """B03-006：Playwright launch_args 含 --ignore-certificate-errors 必须被拒（不启动浏览器）。"""
    import sys
    import types

    config_path = tmp_path / "task.yaml"
    config_path.write_text(
        "project: {name: b306, workspace: work}\n"
        "source: {kind: browser, seeds: [https://example.org/]}\n"
        "browser: {launch_args: ['--ignore-certificate-errors']}\n",
        encoding="utf-8",
    )
    pool = object.__new__(PlaywrightPool)
    pool.config = load_config(config_path)
    pool._queues = [queue.Queue()]
    pool._lock = threading.Lock()
    pool._closed = False
    pool._counter = 0

    launched: list[dict] = []

    class _FakeSyncPW:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return None

        class Chromium:
            @staticmethod
            def launch(**kwargs):
                launched.append(kwargs)
                return object()

    fake = types.ModuleType("playwright")
    fake.sync_api = types.SimpleNamespace(sync_playwright=lambda: _FakeSyncPW())
    monkeypatch.setitem(sys.modules, "playwright", fake)

    work_queue = queue.Queue()
    work_queue.put(None)  # 立即退出 worker
    pool._worker(work_queue)
    assert launched == [], "含 --ignore-certificate-errors 的 launch_args 不应真正启动浏览器"




class _FakePage:
    """最小 page 替身：只实现 _render 会用到的方法。"""

    def __init__(self, *, goto_error: Exception | None = None) -> None:
        self.goto_error = goto_error
        self.closed = False
        self.url = "https://example.org/final"
        self.waits: list[tuple[str | None, int | None]] = []

    def route(self, _pattern, _handler) -> None:
        return None

    def on(self, _event, _handler) -> None:
        return None

    def goto(self, _url, *, wait_until=None, timeout=None):
        self.waits.append((wait_until, timeout))
        if self.goto_error is not None:
            raise self.goto_error
        return type("_Resp", (), {"status": 200})()

    def content(self) -> str:
        return "<html><body>rendered</body></html>"

    def close(self) -> None:
        self.closed = True


class _FakePageContext:
    def __init__(self, page: _FakePage) -> None:
        self._page = page
        self.closed = False

    def new_page(self) -> _FakePage:
        return self._page

    def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    """假 browser：每次 new_context 都给出同一个假 context（供重试路径使用）。"""

    def __init__(self, page: _FakePage) -> None:
        self._page = page

    def new_context(self, **_kwargs):
        return _FakePageContext(self._page)


class _FakeEgress:
    def authorize(self, *_a, **_k) -> None:
        return None

    def record_response(self, *_a, **_k) -> None:
        return None


def _renderable_pool(tmp_path: Path, monkeypatch, page: _FakePage):
    pool = _pool(tmp_path, monkeypatch)
    pool.egress = _FakeEgress()
    saved: list[str] = []
    monkeypatch.setattr(pool, "_save_context", lambda _ctx, key: saved.append(key))
    request = CrawlRequest("https://example.org/")
    key = pool._context_key(request)
    contexts = {key: _FakePageContext(page)}
    return pool, request, contexts, saved


def test_wait_until_timeout_uses_loaded_dom(tmp_path: Path, monkeypatch) -> None:
    """wait_until 超时 ≠ 导航失败：应改用已加载 DOM 继续，而不是整页判失败。

    实测（scrapethissite 国家列表）：networkidle 永不静默 → Page.goto 超时 →
    旧实现视为抓取失败 → 整页 0 条记录。
    """
    page = _FakePage(goto_error=TimeoutError("Page.goto: Timeout 25000ms exceeded"))
    pool, request, contexts, saved = _renderable_pool(tmp_path, monkeypatch, page)

    result = pool._render(object(), contexts, request)

    assert result.body == b"<html><body>rendered</body></html>"
    assert result.status == 200  # goto 未返回 response 时回落 200
    assert page.waits and page.waits[0][0] == "networkidle"
    assert saved, "成功渲染后应保存 context 状态"


def test_non_timeout_goto_error_still_fails(tmp_path: Path, monkeypatch) -> None:
    """非超时异常（DNS / 证书 / 被拦截）必须照旧上抛，不被降级掩盖。"""
    page = _FakePage(goto_error=RuntimeError("net::ERR_NAME_NOT_RESOLVED"))
    pool, request, contexts, _saved = _renderable_pool(tmp_path, monkeypatch, page)

    # 重试的第 2 次也复用同一个假 page/context，最终仍抛原始类型异常
    with pytest.raises(RuntimeError):
        pool._render(_FakeBrowser(page), contexts, request)
