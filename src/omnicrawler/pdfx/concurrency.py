"""Small bounded-concurrency primitives shared by PDF pipeline stages."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import FIRST_COMPLETED, Future, wait


def iter_bounded_futures[ItemT, ResultT](
    items: Iterable[ItemT],
    submit: Callable[[ItemT], Future[ResultT]],
    *,
    max_in_flight: int,
    should_stop: Callable[[], bool] | None = None,
    on_stop: Callable[[], None] | None = None,
) -> Iterator[tuple[Future[ResultT], ItemT]]:
    """Submit at most ``max_in_flight`` jobs and yield them as they finish.

    Stop requests are latched: no new work is consumed after the callback first
    returns true.  Already-running jobs are drained so callers can safely close
    thread-local database connections and persist deterministic outcomes.
    """
    if max_in_flight < 1:
        raise ValueError("max_in_flight 必须大于等于1")

    iterator = iter(items)
    pending: dict[Future[ResultT], ItemT] = {}
    source_exhausted = False
    stop_requested = False

    def poll_stop() -> None:
        nonlocal stop_requested
        if not stop_requested and should_stop is not None and should_stop():
            stop_requested = True
            if on_stop is not None:
                on_stop()

    try:
        while pending or not source_exhausted:
            poll_stop()
            while not stop_requested and not source_exhausted and len(pending) < max_in_flight:
                try:
                    item = next(iterator)
                except StopIteration:
                    source_exhausted = True
                    break
                future = submit(item)
                pending[future] = item
                poll_stop()

            if not pending:
                break

            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in done:
                yield future, pending.pop(future)
    finally:
        close = getattr(iterator, "close", None)
        if callable(close):
            close()
