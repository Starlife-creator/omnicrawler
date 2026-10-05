"""A busy domain must not occupy global slots while waiting for its own slot."""
import asyncio

from omnicrawler.fetching.domain_semaphore import DomainConcurrencyLimiter


def test_waiting_domain_leaves_global_slot_for_healthy_domain():
    async def scenario():
        limiter = DomainConcurrencyLimiter(global_limit=2, per_domain_limit=1)
        entered = asyncio.Event()
        release = asyncio.Event()
        async def slow():
            async with limiter.acquire("https://slow.example/"):
                entered.set()
                await release.wait()
        owner = asyncio.create_task(slow())
        await entered.wait()
        waiting = asyncio.create_task(slow())
        await asyncio.sleep(0)
        healthy = asyncio.Event()
        async def fast():
            async with limiter.acquire("https://healthy.example/"):
                healthy.set()
        fast_task = asyncio.create_task(fast())
        try:
            await asyncio.wait_for(healthy.wait(), 0.2)
        finally:
            release.set()
            await asyncio.gather(owner, waiting, fast_task)
    asyncio.run(scenario())
