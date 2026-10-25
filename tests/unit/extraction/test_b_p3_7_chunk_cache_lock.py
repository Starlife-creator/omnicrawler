"""B-P3-7：ChunkCache 跨线程访问加锁，容量上限不被并发突破。"""

from __future__ import annotations

import threading

from omnicrawler.extraction.ai_chunk_cache import ChunkCache


def test_cache_safe_under_concurrent_put_get() -> None:
    cache = ChunkCache(entries=32, maximum_bytes=1024 * 1024)
    errors: list[Exception] = []

    def worker(worker_id: int) -> None:
        try:
            for index in range(100):
                key = f"w{worker_id}-k{index % 20}"
                cache.put(key, {"v": index})
                cache.get(key)
        except Exception as exc:  # pragma: no cover - 断言失败路径
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert len(cache._values) <= 32
    assert cache._size <= cache.maximum_bytes


def test_cache_lru_eviction_bounded() -> None:
    cache = ChunkCache(entries=3)
    for index in range(5):
        cache.put(f"key{index}", {"v": index})
    assert len(cache._values) == 3
    # 最旧的 key0/key1 已被淘汰，最新三个保留
    assert cache.get("key0") is None
    assert cache.get("key4") is not None
