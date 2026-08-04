from __future__ import annotations

import json
from collections.abc import Iterable
from typing import cast

from ..core.models import CrawlRequest


class RedisFrontier:
    """多worker共享队列。业务处理仍复用CrawlRequest与通用处理器。"""

    def __init__(self, redis_url: str, namespace: str = "omnicrawl") -> None:
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError("缺少redis依赖，请安装 omnicrawl[distributed]") from exc
        self.client = redis.Redis.from_url(redis_url, decode_responses=True)
        self.namespace = namespace
        self.queue = f"{namespace}:frontier"
        self.seen = f"{namespace}:seen"

    def push(self, requests: Iterable[CrawlRequest]) -> int:
        added = 0
        pipe = self.client.pipeline(transaction=False)
        payloads: list[str] = []
        for request in requests:
            pipe.sadd(self.seen, request.fingerprint)
            payload = json.dumps({
                "url": request.url, "method": request.method, "headers": request.headers,
                "kind": request.kind, "render": request.render, "priority": request.priority,
                "depth": request.depth, "parent_url": request.parent_url, "meta": request.meta,
            }, ensure_ascii=False)
            payloads.append(payload)
            pipe.zadd(self.queue, {payload: -request.priority})
        results = pipe.execute()
        # sadd results are at even indices (0, 2, 4, ...); count newly-added members
        for i in range(len(list(requests))):
            idx = i * 2  # sadd is the first command per iteration
            if isinstance(results, list) and idx < len(results) and results[idx]:
                added += 1
        return added

    def pop(self) -> CrawlRequest | None:
        rows = cast(list[tuple[str, float]], self.client.zpopmin(self.queue, 1))
        if not rows:
            return None
        data = json.loads(rows[0][0])
        return CrawlRequest(**data)

    def size(self) -> int:
        return int(cast(int, self.client.zcard(self.queue)))

    def acquire_lock(
        self,
        name: str,
        *,
        timeout_seconds: float = 60,
        blocking_timeout_seconds: float = 0,
    ):
        """Acquire a renewable Redis lock for cross-worker task coordination."""

        lock = self.client.lock(
            f"{self.namespace}:lock:{name}",
            timeout=timeout_seconds,
            blocking_timeout=blocking_timeout_seconds,
            thread_local=False,
        )
        return lock if lock.acquire() else None

    @staticmethod
    def release_lock(lock) -> None:
        if lock is not None and lock.owned():
            lock.release()
