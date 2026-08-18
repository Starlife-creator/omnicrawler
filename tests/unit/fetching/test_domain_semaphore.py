"""P2-3：DomainConcurrencyLimiter 单元测试（双层限速正确性）。"""

from __future__ import annotations

import asyncio


class TestResolveScope:
    def test_http_url_takes_host(self) -> None:
        from omnicrawler.fetching.domain_semaphore import _resolve_scope

        s = _resolve_scope("https://shop.example.com/a/b")
        assert s == "shop.example.com"

    def test_strips_port_and_case(self) -> None:
        from omnicrawler.fetching.domain_semaphore import _resolve_scope

        s = _resolve_scope("https://SHOP.example.COM:8443/x")
        assert s == "shop.example.com"

    def test_relative_or_blank_falls_to_global(self) -> None:
        from omnicrawler.fetching.domain_semaphore import _resolve_scope

        assert _resolve_scope("") == "__global__"
        assert _resolve_scope("urn:isbn:xxx") == "__global__"

    def test_with_site_aliases_collapses(self, monkeypatch) -> None:
        from omnicrawler.core.site_aliases import SiteAliasRegistry
        from omnicrawler.fetching.domain_semaphore import _resolve_scope

        reg = SiteAliasRegistry()
        reg.add_alias("m.shop.example.com", "shop.example.com")
        monkeypatch.setattr(SiteAliasRegistry, "default", staticmethod(lambda: reg))
        assert _resolve_scope("https://m.shop.example.com/") == "shop.example.com"


class TestDomainSemaphore:
    def test_defaults_match_convention(self) -> None:
        from omnicrawler.fetching.domain_semaphore import DomainConcurrencyLimiter

        lim = DomainConcurrencyLimiter(global_limit=8, per_domain_limit=0)
        assert lim.global_limit == 8
        # per_domain_limit 默认 max(1, 8//4) = 2
        assert lim.per_domain_limit == 2
        lim2 = DomainConcurrencyLimiter(global_limit=4, per_domain_limit=0)
        assert lim2.per_domain_limit == 1

    def test_explicit_per_domain_wins(self) -> None:
        from omnicrawler.fetching.domain_semaphore import DomainConcurrencyLimiter

        lim = DomainConcurrencyLimiter(global_limit=8, per_domain_limit=3)
        assert lim.per_domain_limit == 3

    def test_cache_eviction_clears_idle(self) -> None:
        from omnicrawler.fetching.domain_semaphore import DomainConcurrencyLimiter

        lim = DomainConcurrencyLimiter(global_limit=4, per_domain_limit=2, max_cached_domains=4)

        async def worker():
            urls = [f"https://host{i}.example.com/" for i in range(8)]
            for u in urls:
                async with lim.acquire(u):
                    await asyncio.sleep(0)
            # 再触发一轮新域名，迫使 eviction 把部分旧条目踢走
            more = [f"https://extra{i}.example.com/" for i in range(4)]
            for u in more:
                async with lim.acquire(u):
                    await asyncio.sleep(0)

        asyncio.run(worker())
        assert lim.cached_domain_count <= 4

    def test_per_domain_cap_prevents_big_site_starvation(self) -> None:
        """验证单域名不能同时超过 per_domain_limit。"""
        from omnicrawler.fetching.domain_semaphore import DomainConcurrencyLimiter

        lim = DomainConcurrencyLimiter(global_limit=10, per_domain_limit=2)

        entered: list[int] = [0]
        peak: list[int] = [0]

        async def task(i: int) -> None:
            async with lim.acquire("https://A.example.com/page/" + str(i)):
                entered[0] += 1
                if entered[0] > peak[0]:
                    peak[0] = entered[0]
                await asyncio.sleep(0.01)
                entered[0] -= 1

        async def runner():
            await asyncio.gather(*(task(i) for i in range(6)))

        asyncio.run(runner())
        assert peak[0] <= 2

    def test_global_cap_enforced_across_domains(self) -> None:
        """全局上限必须被严格执行。"""
        from omnicrawler.fetching.domain_semaphore import DomainConcurrencyLimiter

        G = 4
        lim = DomainConcurrencyLimiter(global_limit=G, per_domain_limit=99)
        current: list[int] = [0]
        peak: list[int] = [0]

        async def task(host: str) -> None:
            async with lim.acquire(f"https://{host}/x"):
                current[0] += 1
                if current[0] > peak[0]:
                    peak[0] = current[0]
                await asyncio.sleep(0.01)
                current[0] -= 1

        async def runner():
            tasks = []
            for i in range(16):
                tasks.append(task(f"h{i % 4}.example.com"))
            await asyncio.gather(*tasks)

        asyncio.run(runner())
        assert peak[0] <= G
