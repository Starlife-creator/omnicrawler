from __future__ import annotations

import json
from collections.abc import Iterable
from typing import cast

from ..core.models import CrawlRequest

# seen 集合的过期时长（秒）：超时未处理的任务允许重新入队
SEEN_TTL_SECONDS = 7 * 86400


class RedisFrontier:
    """多worker共享队列。业务处理仍复用CrawlRequest与通用处理器。"""

    def __init__(self, redis_url: str, namespace: str = "omnicrawler") -> None:
        try:
            import redis
        except ImportError as exc:
            raise RuntimeError("缺少redis依赖，请安装 omnicrawler[distributed]") from exc
        self.client = redis.Redis.from_url(redis_url, decode_responses=True)
        self.namespace = namespace
        self.queue = f"{namespace}:frontier"
        self.seen = f"{namespace}:seen"
        self.payload = f"{namespace}:payload"

    def push(self, requests: Iterable[CrawlRequest]) -> int:
        requests = list(requests)  # 生成器只消费一次，added 计数不再恒 0（S1.5.5）
        if not requests:
            return 0
        pipe = self.client.pipeline(transaction=True)
        for request in requests:
            pipe.sadd(self.seen, request.fingerprint)
            payload = json.dumps({
                "url": request.url, "method": request.method, "headers": request.headers,
                "kind": request.kind, "render": request.render, "priority": request.priority,
                "depth": request.depth, "parent_url": request.parent_url, "meta": request.meta,
            }, ensure_ascii=False)
            # 队列成员用 fingerprint 去重：同一指纹不会重复入队
            pipe.zadd(self.queue, {request.fingerprint: -request.priority})
            pipe.hset(self.payload, request.fingerprint, payload)
        results = pipe.execute()
        added = 0
        request_list = list(requests)
        for i, _request in enumerate(request_list):
            idx = i * 3  # 每条请求三条命令：sadd / zadd / hset
            if isinstance(results, list) and idx < len(results) and results[idx]:
                added += 1
        # seen 集合带过期，防止不活跃 URL 永不重扫
        pipe = self.client.pipeline(transaction=True)
        pipe.expire(self.seen, SEEN_TTL_SECONDS)
        pipe.expire(self.payload, SEEN_TTL_SECONDS)
        pipe.execute()
        return added

    def pop(self) -> CrawlRequest | None:
        rows = cast(list[tuple[str, float]], self.client.zpopmin(self.queue, 1))
        if not rows:
            return None
        fingerprint = rows[0][0]
        raw = cast(str | None, self.client.hget(self.payload, fingerprint))
        if raw is None:
            return None
        self.client.hdel(self.payload, fingerprint)
        return CrawlRequest(**json.loads(raw))

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
