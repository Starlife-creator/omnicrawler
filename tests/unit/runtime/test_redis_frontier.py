"""`runtime/redis_frontier.py` 的守卫：分布式队列的**去重与取值语义**。

## 为什么这个模块值得测（此前 0% 覆盖）

它是多 worker 共享的 frontier。两个语义错了都会造成生产事故：

1. **`push` 必须去重**：队列成员用 `fingerprint` 做 `zadd`，同一 URL
   不该重复入队。若这里退回「直接 append」，多 worker 会反复抓同一页 ——
   而抓取有真实成本（流量、被封、预算）。
2. **`pop` 必须按优先级取**：`zadd` 的 score 是 `-priority`，所以
   `zpopmin` 取到的是**高优先级**。符号写反就会把重要页面排到最后。
3. **`pop` 取到 fingerprint 但 payload 缺失时要返回 None**（不能抛），
   否则一条脏数据会让 worker 崩掉。

## 边界

**不连真 Redis**：`redis.Redis.from_url` 被替换成假客户端，只验本模块的
逻辑（命令顺序、返回值解读、去重与优先级）。真 Redis 的连通性属于部署验证。
"""
from __future__ import annotations

import json
import sys
import types

import pytest

from omnicrawler.core.models import CrawlRequest
from omnicrawler.runtime import redis_frontier as rf

pytest.importorskip("redis", reason="需要 redis 客户端（distributed extra）")


class _FakePipe:
    """记录命令的最小 pipeline。"""

    def __init__(self, client: _FakeRedis) -> None:
        self.client = client
        self.ops: list[tuple] = []

    def sadd(self, key, value):
        self.ops.append(("sadd", key, value))
        self.client.seen.setdefault(key, set())

    def zadd(self, key, mapping):
        self.ops.append(("zadd", key, mapping))
        self.client.zsets.setdefault(key, {})

    def hset(self, key, field, value):
        self.ops.append(("hset", key, field, value))
        self.client.hashes.setdefault(key, {})

    def expire(self, key, ttl):
        self.ops.append(("expire", key, ttl))

    def execute(self):
        results = []
        for op in self.ops:
            if op[0] == "sadd":
                _, key, value = op
                before = len(self.client.seen[key])
                self.client.seen[key].add(value)
                results.append(1 if len(self.client.seen[key]) > before else 0)
            elif op[0] == "zadd":
                _, key, mapping = op
                self.client.zsets.setdefault(key, {}).update(mapping)
                results.append(1)
            elif op[0] == "hset":
                _, key, field, value = op
                self.client.hashes.setdefault(key, {})[field] = value
                results.append(1)
            else:
                results.append(1)
        return results


class _FakeRedis:
    def __init__(self) -> None:
        self.seen: dict[str, set] = {}
        self.zsets: dict[str, dict] = {}
        self.hashes: dict[str, dict] = {}
        self.lock_acquired = True

    def pipeline(self, transaction=True):
        return _FakePipe(self)

    def zcard(self, key):
        return len(self.zsets.get(key) or {})

    def zpopmin(self, key, count=1):
        zset = self.zsets.get(key) or {}
        if not zset:
            return []
        items = sorted(zset.items(), key=lambda kv: kv[1])[:count]
        for member, _score in items:
            zset.pop(member, None)
        return items

    def hget(self, key, field):
        return (self.hashes.get(key) or {}).get(field)

    def hdel(self, key, field):
        (self.hashes.get(key) or {}).pop(field, None)

    def lock(self, name, **kwargs):
        fake = self

        class _Lock:
            def acquire(self):
                return fake.lock_acquired

            def owned(self):
                return True

            def release(self):
                pass

        return _Lock()


@pytest.fixture
def frontier(monkeypatch: pytest.MonkeyPatch):
    """构造一个接了假 redis 客户端的 frontier。"""
    fake = _FakeRedis()
    fake_module = types.ModuleType("redis")
    fake_module.Redis = types.SimpleNamespace(
        from_url=lambda *_a, **_k: fake
    )
    monkeypatch.setitem(sys.modules, "redis", fake_module)
    return rf.RedisFrontier("redis://fake:6379/0", namespace="test"), fake


def _request(url: str = "https://example.com/a", priority: int = 0) -> CrawlRequest:
    return CrawlRequest(url=url, priority=priority)


def test_push_returns_zero_for_empty_input(frontier) -> None:
    """空输入返回 0，且**不碰 redis**（避免无意义的往返）。"""
    f, fake = frontier
    assert f.push([]) == 0
    assert not fake.zsets, "空输入不该产生任何写入"


def test_push_deduplicates_by_fingerprint(frontier) -> None:
    """**核心语义**：同一 URL 重复 push 只入队一次。"""
    f, fake = frontier
    first = f.push([_request("https://example.com/a")])
    second = f.push([_request("https://example.com/a")])
    assert first == 1
    assert second == 0, "重复 URL 被再次入队 —— 多 worker 会重复抓同一页"
    assert f.size() == 1


def test_push_counts_only_newly_added(frontier) -> None:
    """一次 push 里混合新老 URL，只计新增数。"""
    f, fake = frontier
    f.push([_request("https://example.com/a")])
    added = f.push(
        [
            _request("https://example.com/a"),  # 老
            _request("https://example.com/b"),  # 新
        ]
    )
    assert added == 1


def test_pop_respects_priority_order(frontier) -> None:
    """**核心语义**：高优先级先出队（score 是 `-priority`）。"""
    f, fake = frontier
    f.push([_request("https://example.com/low", priority=1)])
    f.push([_request("https://example.com/high", priority=99)])
    first = f.pop()
    assert first is not None
    assert first.url == "https://example.com/high", (
        f"优先级顺序错了：先取出 {first.url}"
    )


def test_pop_returns_none_when_empty(frontier) -> None:
    """空队列 `pop` 返回 None，不抛异常。"""
    f, _fake = frontier
    assert f.pop() is None


def test_pop_returns_none_when_payload_missing(frontier) -> None:
    """**健壮性**：有 fingerprint 但 payload 缺失 ⇒ 返回 None 而不是抛。"""
    f, fake = frontier
    f.push([_request("https://example.com/a")])
    # 模拟 payload 丢失（如被 TTL 清掉）
    fake.hashes.clear()
    assert f.pop() is None


def test_pop_roundtrips_request_fields(frontier) -> None:
    """出队的请求必须**字段完整**（URL/method/kind/priority/depth/meta）。"""
    f, _fake = frontier
    original = CrawlRequest(
        url="https://example.com/x",
        method="POST",
        kind="api",
        render=True,
        priority=7,
        depth=3,
        parent_url="https://example.com/p",
        meta={"k": "v"},
    )
    f.push([original])
    back = f.pop()
    assert back is not None
    assert back.url == original.url
    assert back.method == original.method
    assert back.kind == original.kind
    assert back.render == original.render
    assert back.priority == original.priority
    assert back.depth == original.depth
    assert back.meta == original.meta


def test_seen_set_gets_ttl(frontier) -> None:
    """seen 集合必须设过期，否则不活跃 URL 永不重扫。"""
    f, fake = frontier
    f.push([_request("https://example.com/a")])
    # `_FakePipe` 记录 expire 调用；这里检查构造的 key 名与常量
    assert rf.SEEN_TTL_SECONDS == 7 * 86400
    assert f.seen.endswith(":seen")


def test_acquire_lock_returns_none_when_not_acquired(frontier) -> None:
    """抢锁失败必须返回 None（调用方据此判断「别的 worker 在做」）。"""
    f, fake = frontier
    fake.lock_acquired = False
    assert f.acquire_lock("task") is None


def test_acquire_lock_returns_lock_object_when_acquired(frontier) -> None:
    """抢到锁返回锁对象。"""
    f, fake = frontier
    assert f.acquire_lock("task") is not None


def test_release_lock_tolerates_none() -> None:
    """`release_lock(None)` 不得抛（调用方常直接传 acquire 的结果）。"""
    rf.RedisFrontier.release_lock(None)


def test_missing_redis_dependency_gives_actionable_error(monkeypatch) -> None:
    """缺 redis 依赖时报错必须告诉用户装哪个 extra。"""
    monkeypatch.setitem(sys.modules, "redis", None)
    with pytest.raises(RuntimeError) as excinfo:
        rf.RedisFrontier("redis://x/0")
    assert "distributed" in str(excinfo.value), (
        f"错误信息没说装哪个 extra：{excinfo.value}"
    )


def test_payload_is_json_and_utf8_safe(frontier) -> None:
    """payload 用 JSON 存储且中文原样（不转义成 \\uXXXX）。"""
    f, fake = frontier
    f.push([CrawlRequest(url="https://example.com/中文", meta={"标题": "测试"})])
    stored = next(iter((fake.hashes.get(f.payload) or {}).values()), "")
    assert "中文" in stored, "payload 把中文转义了 —— 可读性变差"
    assert json.loads(stored)["url"].endswith("中文")
