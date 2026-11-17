"""B-P2-3：并发轮次整体超时——挂死 future 被取消并作为失败项交出，释放 worker。

pytesseract 真实子进程超时需 OCR 环境验证；本测试覆盖调度层超时取消逻辑。
"""

from __future__ import annotations

import concurrent.futures

from omnicrawler.pdfx.concurrency import iter_bounded_futures


def test_future_timeout_cancels_stuck_futures() -> None:
    def submit(_item: int) -> concurrent.futures.Future[int]:
        return concurrent.futures.Future()

    completed = list(iter_bounded_futures(
        range(3), submit, max_in_flight=3, future_timeout=0.05,
    ))

    assert len(completed) == 3
    assert all(future.cancelled() for future, _item in completed)


def test_no_timeout_keeps_existing_completion_contract() -> None:
    def submit(item: int) -> concurrent.futures.Future[int]:
        future: concurrent.futures.Future[int] = concurrent.futures.Future()
        future.set_result(item * 2)
        return future

    completed = list(iter_bounded_futures(range(3), submit, max_in_flight=3))
    assert sorted(future.result() for future, _item in completed) == [0, 2, 4]
