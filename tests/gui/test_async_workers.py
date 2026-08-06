from __future__ import annotations

from omnicrawl.gui.async_workers import AsyncWorkerManager


class _FakeThread:
    def __init__(self, *, stops: bool) -> None:
        self.stops = stops
        self.interrupted = 0
        self.quit_called = 0
        self.wait_calls: list[int] = []

    def requestInterruption(self) -> None:
        self.interrupted += 1

    def quit(self) -> None:
        self.quit_called += 1

    def wait(self, timeout_ms: int) -> bool:
        self.wait_calls.append(timeout_ms)
        return self.stops


def test_cancel_all_interrupts_and_waits_all_workers() -> None:
    manager = AsyncWorkerManager()
    first = _FakeThread(stops=True)
    second = _FakeThread(stops=True)
    manager._active_workers = [first, second]  # type: ignore[attr-defined]

    remaining = manager.cancel_all(timeout_ms=1500)

    assert remaining == ()
    assert first.interrupted == 1 and second.interrupted == 1
    assert first.quit_called == 1 and second.quit_called == 1
    assert first.wait_calls == [1500] and second.wait_calls == [1500]


def test_cancel_all_returns_threads_exceeding_single_budget() -> None:
    """S1.1.5：未能在单次总预算内停止的线程被返回并保留跟踪。"""
    manager = AsyncWorkerManager()
    stuck = _FakeThread(stops=False)
    manager._active_workers = [stuck]  # type: ignore[attr-defined]

    remaining = manager.cancel_all(timeout_ms=500)

    assert remaining == (stuck,)
    assert manager._active_workers == [stuck]  # type: ignore[attr-defined]


def test_cancel_all_with_no_workers() -> None:
    manager = AsyncWorkerManager()
    assert manager.cancel_all() == ()
