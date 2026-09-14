"""变更监测的关闭路径：不得在关窗后留下在飞的后台工作。

## 为什么要有这组用例（背景）

CI 上 `test_visual_theme_home_transition_and_help_visibility` 长期红：一次检查的抓取在
`window.close()` 之后仍在跑，进程收尾时触发 `Fatal Python error: Aborted`。机制核实（2026-09-14）：

* 视图**构造时就起 30s 轮询定时器**，且**没有关闭入口** ⇒ 关闭流程之后仍可能启动一次检查；
* `_CheckWorker` **不理会 `requestInterruption()`**（实测：中断后仍等满整次抓取）；
* `ChangeDetector` **没有取消/等待入口**；
* 实测澄清一点：`asyncio.run()` **会** join 线程池那一跳，所以"那一跳泄漏"不成立
  ——真正的缺口是「关闭后仍可能启动新检查 + 无法请求取消」。

**这组断言与镜像无关**：不依赖 certifi/CA 状态，只断言"关窗后没有在飞的后台工作"，
因此本地就能跑、能验（不必等 CI）。判定用 `asyncio_*` 线程是否残留 + worker 终态。
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FETCH_SECONDS = 0.6


def _wait_until(predicate, *, timeout: float = 5.0, interval: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _pool_threads() -> list[str]:
    """仍在运行的 asyncio/线程池线程（抓取真正在飞的标志）。"""
    return [
        thread.name
        for thread in threading.enumerate()
        if "asyncio" in thread.name or "ThreadPoolExecutor" in thread.name
    ]


def _rule(url: str, rule_id: str = "r-1") -> dict:
    return {"rule_id": rule_id, "name": rule_id, "url": url, "enabled": True}


class _StubFetcher:
    """桩抓取：每个 URL 睡 `FETCH_SECONDS`，并记录被请求过的 URL。"""

    def __init__(self) -> None:
        self.requested: list[str] = []
        self._lock = threading.Lock()

    def fetch(self, request):  # noqa: ANN001, ANN201
        with self._lock:
            self.requested.append(str(request.url))
        time.sleep(FETCH_SECONDS)

        class _Body:
            headers = {"content-type": "text/html; charset=utf-8"}
            body = b"<html><body><p>stub</p></body></html>"

        return _Body()


# ── ① 关窗后不得再发起网络 I/O（前缀守卫，与镜像无关）────────────────────


def test_cancel_before_start_skips_every_fetch() -> None:
    """`start()` 之前就取消 ⇒ **一次抓取都不发**（这是"关闭后不再联网"的入口防线）。"""
    from omnicrawler.gui.views.change_monitor import _CheckWorker

    fetcher = _StubFetcher()
    worker = _CheckWorker([_rule("https://example.invalid/a")], None, fetcher=fetcher)
    worker.cancel()
    worker.run()  # 同步执行：逻辑类断言用 run()（见技能：中断才必须 start()）

    assert fetcher.requested == [], f"取消后仍在抓取：{fetcher.requested}"


def test_detector_cancel_makes_fetch_content_a_noop() -> None:
    """detector 层：取消后 `_fetch_content` 直接返回 None，不触碰 fetcher。"""
    from omnicrawler.scheduling.change_detector import ChangeDetector

    fetcher = _StubFetcher()
    detector = ChangeDetector(fetcher=fetcher)
    detector.cancel()
    assert detector.cancelled is True
    assert detector._fetch_content.__name__ == "_fetch_content"  # 存在性（防止误删）
    result = _run_coro(detector._fetch_content("https://example.invalid/b"))
    assert result is None
    assert fetcher.requested == []


def _run_coro(coro):  # noqa: ANN001, ANN202
    import asyncio

    return asyncio.run(coro)


# ── ② 取消不冒充成功 + 不再开下一条规则 ──────────────────────────────────


def test_cancel_mid_flight_drops_payload_and_skips_remaining_rules() -> None:
    """在飞时取消：**不把结果当成功交付**，且不再发起下一条规则的抓取。

    取消无法中断已经发出的请求（阻塞调用），所以第一条会跑完；关键是第二条不得再发。
    """
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.views.change_monitor import _CheckWorker

    app = QApplication.instance() or QApplication([])
    fetcher = _StubFetcher()
    worker = _CheckWorker(
        [_rule("https://example.invalid/first", "r-1"), _rule("https://example.invalid/second", "r-2")],
        None,
        fetcher=fetcher,
    )
    delivered: list[list] = []
    worker.finished.connect(delivered.append)

    worker.start()
    assert _wait_until(lambda: len(fetcher.requested) >= 1, timeout=5), "第一次抓取未开始"
    worker.cancel()
    assert worker.wait(10_000), "取消后 worker 未在超时内结束"
    QTest.qWait(50)  # wait() 会挡住队列信号（见技能陷阱 2）

    assert fetcher.requested == ["https://example.invalid/first"], (
        f"取消后不应抓取后续规则：{fetcher.requested}"
    )
    assert delivered == [[]], f"取消时不得把（截断的）结果当成功交付：{delivered}"
    assert _wait_until(lambda: not _pool_threads(), timeout=5), "worker 结束后仍有残留线程"
    app.processEvents()


# ── ③ 视图 shutdown：停轮询、等在飞、之后不再启动 ────────────────────────


def test_view_shutdown_stops_polling_and_leaves_no_inflight_work() -> None:
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.views.change_monitor import ChangeMonitorView

    app = QApplication.instance() or QApplication([])
    view = ChangeMonitorView()
    fetcher = _StubFetcher()
    view._fetcher = fetcher
    view._rules_data = [_rule("https://example.invalid/view")]

    view._check_all()
    assert _wait_until(lambda: len(fetcher.requested) == 1, timeout=5), "视图检查未开始"

    view.shutdown()
    assert view._timer.isActive() is False, "shutdown 必须停掉 30s 轮询"
    assert view._worker is None or not view._worker.isRunning(), "shutdown 后不得仍有检查在跑"
    assert _wait_until(lambda: not _pool_threads(), timeout=5), f"仍有残留线程：{_pool_threads()}"

    # 关闭后再触发一次轮询/手动检查，都不得启动新工作
    fetcher.requested.clear()
    view._periodic_check()
    view._check_all()
    view._check_single("r-1")
    QTest.qWait(120)
    assert fetcher.requested == [], f"关闭后不得再发起抓取：{fetcher.requested}"
    assert view._worker is None or not view._worker.isRunning()
    app.processEvents()


# ── ④ 主窗口关闭路径（CI 那条失败的形状）────────────────────────────────


def test_main_window_close_leaves_no_inflight_monitor_work(tmp_path: Path, monkeypatch) -> None:
    """窗口关闭时若一次检查在飞：关闭返回后**不得**还有在飞的后台工作。

    这条就是 CI 那条失败的形状（关闭 + 抓取在飞），但断言与镜像无关 ⇒ 本地可验。
    """
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.main import MainWindow

    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(MainWindow, "_on_first_launch", lambda self: None)
    window = MainWindow()
    window._project_root = tmp_path
    window._rebuild_project_components()

    monitor = window._change_monitor
    fetcher = _StubFetcher()
    monitor._fetcher = fetcher
    monitor._rules_data = [_rule("https://example.invalid/close")]
    monitor._check_all()
    assert _wait_until(lambda: len(fetcher.requested) == 1, timeout=5), "监测检查未开始"

    window.close()
    app.processEvents()

    assert monitor._shutting_down is True, "关闭流程必须停掉变更监测"
    assert monitor._timer.isActive() is False
    assert monitor._worker is None or not monitor._worker.isRunning()
    assert _wait_until(lambda: not _pool_threads(), timeout=5), (
        f"关窗后仍有在飞的后台工作：{_pool_threads()}"
    )


# ── ⑤ 迟到信号守卫（反向用例）────────────────────────────────────────────


def test_late_finished_from_old_worker_is_ignored() -> None:
    """旧 worker 迟到的终态不得改动"当前 worker"的引用（否则新检查会被静默解除防护）。"""
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.views.change_monitor import ChangeMonitorView, _CheckWorker

    app = QApplication.instance() or QApplication([])
    view = ChangeMonitorView()
    current = _CheckWorker([], view)
    view._worker = current
    stale = _CheckWorker([], view)
    stale.finished.connect(view._on_check_finished)

    stale.finished.emit([])  # 旧任务的迟到信号

    assert view._worker is current, "迟到信号把当前 worker 引用清掉了"
    app.processEvents()


@pytest.mark.parametrize("wait_ms", [1])
def test_shutdown_wait_is_bounded(wait_ms: int) -> None:
    """等待是**有界**的：抓取远比 wait_ms 慢时，shutdown 不阻塞退出（只记日志）。"""
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.views.change_monitor import ChangeMonitorView

    app = QApplication.instance() or QApplication([])
    view = ChangeMonitorView()
    fetcher = _StubFetcher()
    view._fetcher = fetcher
    view._rules_data = [_rule("https://example.invalid/slow")]
    view._check_all()
    assert _wait_until(lambda: len(fetcher.requested) == 1, timeout=5)

    started = time.monotonic()
    view.shutdown(wait_ms=wait_ms)
    elapsed = time.monotonic() - started

    assert elapsed < FETCH_SECONDS, f"有界等待失效：阻塞了 {elapsed:.2f}s"
    # 收尾：让在飞抓取自然结束，避免影响后续用例
    assert _wait_until(lambda: not _pool_threads(), timeout=5)
    app.processEvents()
