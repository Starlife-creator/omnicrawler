"""P2-3：按域名独立并发配额（双层 Semaphore）。

借鉴 Colly 的「Per-host concurrency limit + Global limit」思路：
    * 全局 Semaphore：控制总并发（不打爆本机/出口带宽），等价 Colly 的 global semaphore
    * 按域名（经 P2-5 SiteAliasRegistry 归并后）独立 Semaphore：避免某大站
      占满所有并发位，其他小站点被饿死

严格合规边界：
    * 仅做限速，不做任何请求修改/伪装/优先级歧视
    * 域名取自已审批过的请求 URL；与 egress.policy 白名单互不相干

使用：
    limiter = DomainConcurrencyLimiter(global_limit=8, per_domain_limit=2)
    async with limiter.acquire(url):
        result = await do_request(url)
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlsplit

__all__ = ["DomainConcurrencyLimiter"]

# 安全导入 site_aliases：不可用时退化到纯 normalize_host
try:
    from ..core.site_aliases import SiteAliasRegistry, normalize_host
except Exception:  # noqa: BLE001
    def normalize_host(host: str) -> str:  # type: ignore[misc]
        return (host or "").strip().rstrip(".").casefold()

    SiteAliasRegistry = None  # type: ignore[assignment,misc]


def _resolve_scope(url: str, *, environment: str | None = None) -> str:
    """从 URL 取出 host，经 SiteAliasRegistry 归并（若可用）。"""
    host = (urlsplit(url).hostname or "").casefold()
    if not host:
        # 非 HTTP URL 或缺失 host：落到单 bucket "__global__" 走统一限速
        return "__global__"
    h = normalize_host(host)
    if SiteAliasRegistry is not None:
        try:
            return SiteAliasRegistry.default().resolve(h, environment=environment) or h
        except Exception:  # noqa: BLE001
            pass
    return h


class DomainConcurrencyLimiter:
    """双层并发限速器：全局 + 按域名（可配置 per-domain 上限）。

    Parameters
    ----------
    global_limit:
        总并发上限（默认 4，与 AsyncFetcher 默认 concurrency 对齐）。
    per_domain_limit:
        单域名并发上限；<=0 时退化为 ``max(1, global_limit // 4)``。
    max_cached_domains:
        最多缓存多少域名的 Semaphore（避免长尾域名无限增长内存）。
        超出时 LRU 清除未使用的条目；默认 256。
    environment:
        可选环境标签（传递给 SiteAliasRegistry）。
    """

    __slots__ = (
        "_global_sem",
        "_per_limit",
        "_max_cache",
        "_env",
        "_sems",
        "_lock",  # 同步锁：保护 _sems dict（async 侧不应并发 mutate，加一层防御）
    )

    def __init__(
        self,
        global_limit: int = 4,
        *,
        per_domain_limit: int = 0,
        max_cached_domains: int = 256,
        environment: str | None = None,
    ) -> None:
        g = max(1, int(global_limit))
        p = int(per_domain_limit) if per_domain_limit > 0 else max(1, g // 4)
        self._global_sem = asyncio.Semaphore(g)
        self._per_limit = p
        mc = int(max_cached_domains)
        self._max_cache = mc if mc >= 2 else 2
        self._env = environment
        # host → asyncio.Semaphore
        self._sems: dict[str, asyncio.Semaphore] = {}
        self._lock = threading.Lock()

    # ── 公共属性（用于检查/测试） ─────────────────────────
    @property
    def global_limit(self) -> int:
        return self._global_sem._value  # type: ignore[attr-defined]

    @property
    def per_domain_limit(self) -> int:
        return self._per_limit

    @property
    def cached_domain_count(self) -> int:
        return len(self._sems)

    # ── 核心上下文 ────────────────────────────────────────
    @asynccontextmanager
    async def acquire(self, url: str) -> AsyncIterator[None]:
        """获取「全局 + 域名」双层信号量，退出时自动释放。

        order：先拿全局（防止单域名饿死小域名→全局槽被大站点全占），
        再拿域名（保证单域名不会占满全局）。
        注：如果顺序反过来「先拿域名再拿全局」，多个域名都能先各自进入等待队列，
        最终等待全局那一把，效果相同；但 Colly/大多数实现都采用 global-first。
        """
        scope = _resolve_scope(url, environment=self._env)
        domain_sem = self._get_or_create_sem(scope)
        # 先全局，再域名
        async with self._global_sem:
            async with domain_sem:
                yield

    # ── 内部 ──────────────────────────────────────────────
    def _get_or_create_sem(self, scope: str) -> asyncio.Semaphore:
        # 先乐观读取（无锁，避免每次都开销）
        sem = self._sems.get(scope)
        if sem is not None:
            return sem
        with self._lock:
            sem = self._sems.get(scope)
            if sem is not None:
                return sem
            # 超上限时，先做一轮清理：挑一个内部 value == per_limit（即空闲）的删掉
            if len(self._sems) >= self._max_cache:
                self._evict_idle_locked()
            sem = asyncio.Semaphore(self._per_limit)
            self._sems[scope] = sem
            return sem

    def _evict_idle_locked(self) -> None:
        # 清除 idle（Semaphore value == _per_limit，即所有持有者均已释放）
        # 最多清 max_cache/4 个，避免单次大扫描
        target = max(1, self._max_cache // 4)
        removed = 0
        for host in list(self._sems.keys()):
            if removed >= target:
                break
            s = self._sems[host]
            try:
                if s._value >= self._per_limit:  # type: ignore[attr-defined]
                    del self._sems[host]
                    removed += 1
            except AttributeError:
                # 底层 Semaphore 没有 _value 时（非标准实现），保守跳过
                continue
