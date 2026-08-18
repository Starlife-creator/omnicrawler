"""P2-3：FetchHooks 三阶段钩子单元测试（异常隔离 / 同步异步混合 / extend）。"""

from __future__ import annotations

import asyncio

import pytest


class TestFetchHooks:
    def test_empty_is_empty(self) -> None:
        from omnicrawler.fetching.hooks import FetchHooks

        h = FetchHooks()
        assert h.is_empty() is True
        h.on_before_fetch(lambda req, ctx: None)
        assert h.is_empty() is False

    def test_sync_before_and_after_run_in_order(self) -> None:
        from omnicrawler.core.models import CrawlRequest, FetchResult
        from omnicrawler.fetching.hooks import FetchHooks

        h = FetchHooks()
        events: list[str] = []

        @h.on_before_fetch
        def b(req, ctx):
            events.append("b1")

        @h.on_after_fetch
        def a(req, result, ctx):
            events.append("a1")

        req = CrawlRequest(url="https://x/")

        async def runner():
            req2 = await h.emit_before_fetch(req, {})
            assert req2 is req
            result = FetchResult(req, str(req.url), 200, {}, b"ok", 0.01)
            await h.emit_after_fetch(req, result, {})

        asyncio.run(runner())
        assert events == ["b1", "a1"]

    def test_async_hooks_awaited(self) -> None:
        from omnicrawler.core.models import CrawlRequest
        from omnicrawler.fetching.hooks import FetchHooks

        h = FetchHooks()
        order: list[int] = []

        @h.on_before_fetch
        async def slow_before(req, ctx):
            await asyncio.sleep(0.01)
            order.append(1)

        @h.on_before_fetch
        def quick_before(req, ctx):
            order.append(2)

        async def runner():
            await h.emit_before_fetch(CrawlRequest(url="https://x/"), {})

        asyncio.run(runner())
        # slow_before 先注册 → 先 await → 在 quick_before 之前
        assert order == [1, 2]

    def test_single_hook_exception_isolated_no_affect_others(self) -> None:
        from omnicrawler.core.models import CrawlRequest
        from omnicrawler.fetching.hooks import FetchHooks

        h = FetchHooks()
        @h.on_before_fetch
        def bad(req, ctx):
            raise RuntimeError("boom")

        called: list[bool] = [False]
        @h.on_before_fetch
        def good(req, ctx):
            called[0] = True

        async def runner():
            return await h.emit_before_fetch(CrawlRequest(url="https://x/"), {})

        # 主流程绝不抛
        req = asyncio.run(runner())
        assert req is not None
        assert called[0] is True

    def test_error_hook_sees_exception(self) -> None:
        from omnicrawler.core.models import CrawlRequest
        from omnicrawler.fetching.hooks import FetchHooks

        h = FetchHooks()
        seen: list[Exception] = []
        @h.on_error
        def handle(req, exc, ctx):
            seen.append(exc)

        async def runner():
            try:
                raise ValueError("oops")
            except Exception as exc:
                await h.emit_error(CrawlRequest(url="https://x/"), exc, {})
                raise

        with pytest.raises(ValueError):
            asyncio.run(runner())
        assert len(seen) == 1
        assert isinstance(seen[0], ValueError)

    def test_error_hook_itself_failing_is_also_isolated(self) -> None:
        from omnicrawler.core.models import CrawlRequest
        from omnicrawler.fetching.hooks import FetchHooks

        h = FetchHooks()
        @h.on_error
        def bad(req, exc, ctx):
            raise ZeroDivisionError

        second_ok: list[bool] = [False]
        @h.on_error
        def second(req, exc, ctx):
            second_ok[0] = True

        async def runner():
            try:
                raise ValueError("orig")
            except Exception as exc:
                await h.emit_error(CrawlRequest(url="https://x/"), exc, {})
                raise

        with pytest.raises(ValueError, match="orig"):  # 原始异常（非 ZeroDivisionError）
            asyncio.run(runner())
        assert second_ok[0] is True

    def test_extend_composes_hooks(self) -> None:
        from omnicrawler.core.models import CrawlRequest
        from omnicrawler.fetching.hooks import FetchHooks

        h1 = FetchHooks()
        h2 = FetchHooks()
        call: list[str] = []

        @h1.on_before_fetch
        def a(req, ctx):
            call.append("h1")

        @h2.on_before_fetch
        def b(req, ctx):
            call.append("h2")

        h1.extend(h2)
        asyncio.run(h1.emit_before_fetch(CrawlRequest(url="https://x/"), {}))
        assert call == ["h1", "h2"]
