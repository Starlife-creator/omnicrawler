"""Phase 2b N3 keepalive 长驻会话池契约测试（跨 run 复用 + idle 回收 + hook 分发）。"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from omnicrawler.plugins.plugin_keepalive import (
    KeepaliveSessionPool,
    dispatch_hook_event,
)

pytestmark = pytest.mark.plugin_contract


@pytest.fixture()
def plugin_dir(tmp_path: Path) -> Path:
    plugin = tmp_path / "keepalive_plugin"
    plugin.mkdir()
    (plugin / "k_plugin.py").write_text(
        textwrap.dedent(
            """
            def handle(operation, payload):
                if operation == "source.seed":
                    return {"requests": [{"url": "https://example.com/"}]}
                if operation == "hook.after_fetch":
                    return {"hook_received": payload.get("event_data")}
                return {"ok": True}
            """
        ),
        encoding="utf-8",
    )
    return plugin


def test_acquire_reuse_same_process(plugin_dir: Path) -> None:
    """跨 run 复用：第二次 acquire 复用同一长驻会话（进程 PID 一致）。"""
    pool = KeepaliveSessionPool()
    first = pool.acquire("p1", plugin_root=plugin_dir, entry_module="k_plugin", permissions=set())
    first.call("source.seed", {})
    pool.release("p1", first)
    first_pid = first._session._proc.pid  # noqa: SLF001

    second = pool.acquire("p1", plugin_root=plugin_dir, entry_module="k_plugin", permissions=set())
    assert second is first, "keepalive 应复用同一 host"
    assert second._session._proc.pid == first_pid  # noqa: SLF001
    pool.release("p1", second)
    pool.close_all()


def test_idle_timeout_reaps_session(plugin_dir: Path) -> None:
    """idle 超时回收：超时后会话关闭、名额释放。"""
    pool = KeepaliveSessionPool(idle_timeout_seconds=0.1)
    host = pool.acquire("p1", plugin_root=plugin_dir, entry_module="k_plugin", permissions=set())
    host.call("source.seed", {})  # 触发懒 spawn
    pool.release("p1", host)
    assert pool.stats()["idle"] == 1
    import time

    time.sleep(0.3)
    reaped = pool.reap()
    assert reaped == 1
    assert pool.stats()["idle"] == 0
    pool.close_all()


def test_concurrency_limit(plugin_dir: Path) -> None:
    """并发上限：超过 max_concurrent 拒绝（keepalive 计入名额）。"""
    pool = KeepaliveSessionPool(max_concurrent=1)
    first = pool.acquire("p1", plugin_root=plugin_dir, entry_module="k_plugin", permissions=set())
    with pytest.raises(RuntimeError, match="并发已达上限"):
        pool.acquire("p2", plugin_root=plugin_dir, entry_module="k_plugin", permissions=set())
    pool.release("p1", first)
    pool.close_all()


def test_dispatch_hook_event(plugin_dir: Path) -> None:
    """hook 事件分发：handle('hook.after_fetch', {event_data}) → 结果回传。"""
    pool = KeepaliveSessionPool()
    host = pool.acquire("p1", plugin_root=plugin_dir, entry_module="k_plugin", permissions=set())
    result = dispatch_hook_event(host, "after_fetch", {"event_data": {"url": "https://x"}})
    assert result == {"hook_received": {"url": "https://x"}}
    pool.release("p1", host)
    pool.close_all()


def test_close_all_reclaims(plugin_dir: Path) -> None:
    pool = KeepaliveSessionPool()
    host = pool.acquire("p1", plugin_root=plugin_dir, entry_module="k_plugin", permissions=set())
    pool.release("p1", host)
    pool.close_all()
    assert pool.stats()["idle"] == 0
    assert pool.stats()["active"] == 0
    with pytest.raises(RuntimeError, match="已关闭"):
        pool.acquire("p1", plugin_root=plugin_dir, entry_module="k_plugin", permissions=set())
