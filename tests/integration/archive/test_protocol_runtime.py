from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import yaml

from omnicrawl.core.config import load_config
from omnicrawl.core.errors import PermanentFetchError, ResponseTooLargeError
from omnicrawl.core.models import CrawlRequest
from omnicrawl.fetching.async_fetcher import HTTPXAsyncFetcher
from omnicrawl.fetching.streams import collect_sse, collect_websocket
from omnicrawl.runtime.redis_frontier import RedisFrontier
from omnicrawl.sources.frameworks import run_scrapy


def _config(tmp_path: Path, *, source=None, http=None):
    value = {
        "project": {"name": "protocol-test", "workspace": str(tmp_path / "workspace")},
        "source": {"kind": "static_html", "seeds": ["https://example.org"]},
        "http": {
            "resolve_dns": False,
            "respect_robots": False,
            "delay_seconds": 0,
            "retries": 1,
            "max_redirects": 2,
            "max_response_bytes": 1024,
        },
        "crawl": {"concurrency": 2},
    }
    if source:
        value["source"].update(source)
    if http:
        value["http"].update(http)
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(value), encoding="utf-8")
    return load_config(path)


class _LineResponse:
    def __init__(self, lines):
        self.lines = iter(lines)
        self.readline_calls = 0

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def readline(self):
        self.readline_calls += 1
        return next(self.lines, b"")


def test_sse_collects_events_and_honors_limits(tmp_path: Path) -> None:
    config = _config(tmp_path, source={"max_messages": 2, "duration_seconds": 5})
    response = _LineResponse(
        [
            b": comment\n",
            b"event: update\n",
            b"data: one\n",
            b"data: two\n",
            b"\n",
            b"data: final\n",
            b"\n",
        ]
    )
    opener = MagicMock()
    opener.open.return_value = response
    with patch("omnicrawl.fetching.streams.build_safe_opener", return_value=opener):
        records = collect_sse(config, CrawlRequest("https://example.org/events"))
    assert len(records) == 2
    assert records[0].data == {"event": "update", "data": "one\ntwo"}
    assert records[1].data == {"data": "final"}

    stopped = _LineResponse([b"data: ignored\n", b"\n"])
    opener.open.return_value = stopped
    with patch("omnicrawl.fetching.streams.build_safe_opener", return_value=opener):
        assert collect_sse(
            config, CrawlRequest("https://example.org/events"), should_continue=lambda: False
        ) == []

    tiny = _config(tmp_path, http={"max_response_bytes": 1024})
    oversized = _LineResponse([b"data: " + (b"x" * 1100) + b"\n"])
    opener.open.return_value = oversized
    with patch("omnicrawl.fetching.streams.build_safe_opener", return_value=opener):
        with pytest.raises(ValueError, match="SSE"):
            collect_sse(tiny, CrawlRequest("https://example.org/events"))


def test_sse_breaks_out_of_busy_loop_on_eof(tmp_path: Path) -> None:
    """S1.4.3：服务端断开（EOF 空读）后不再忙循环，循环尽快退出。"""
    hung_up = _LineResponse([])
    opener = MagicMock()
    opener.open.return_value = hung_up
    with patch("omnicrawl.fetching.streams.build_safe_opener", return_value=opener):
        records = collect_sse(
            _config(tmp_path, source={"max_messages": 100, "duration_seconds": 60}),
            CrawlRequest("https://example.org/events"),
        )
    assert records == []
    assert hung_up.readline_calls <= 5  # 阈值内即退出，而非烧完整段 timeout

    # 正常事件流之间出现较多空行也不受影响
    heartbeat = _LineResponse(
        [b"data: keepalive\n", b"\n", b"\n", b"\n", b"data: still-alive\n", b"\n"]
    )
    opener.open.return_value = heartbeat
    with patch("omnicrawl.fetching.streams.build_safe_opener", return_value=opener):
        records = collect_sse(
            _config(tmp_path, source={"max_messages": 10, "duration_seconds": 5}),
            CrawlRequest("https://example.org/events"),
        )
    assert [item.data for item in records] == [{"data": "keepalive"}, {"data": "still-alive"}]


class _FakeSocket:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.sent = []

    async def send(self, value):
        self.sent.append(value)

    async def recv(self):
        return next(self.messages)


class _FakeConnect:
    def __init__(self, socket):
        self.socket = socket

    async def __aenter__(self):
        return self.socket

    async def __aexit__(self, *_args):
        return None


def test_websocket_collects_json_text_binary_and_subscribes(tmp_path: Path, monkeypatch) -> None:
    websockets = pytest.importorskip("websockets")
    config = _config(
        tmp_path,
        source={
            "kind": "websocket",
            "max_messages": 3,
            "duration_seconds": 5,
            "subscribe": {"topic": "demo"},
            "headers": {"X-Source": "yes"},
        },
    )
    socket = _FakeSocket(['{"id": 1}', "plain", b"\x01\x02"])
    monkeypatch.setattr(websockets, "connect", lambda *_args, **_kwargs: _FakeConnect(socket))
    records = collect_websocket(config, CrawlRequest("wss://example.org/socket"))
    assert [item.data for item in records] == [
        {"id": 1},
        {"text": "plain"},
        {"binary_hex": "0102", "size": 2},
    ]
    assert socket.sent == [json.dumps({"topic": "demo"}, ensure_ascii=False)]


class _FakePipeline:
    def __init__(self, client):
        self.client = client
        self.items = []  # list of (cmd, *args)
        self.executed = False

    def sadd(self, name, value):
        self.items.append(("sadd", name, value))

    def zadd(self, queue, mapping):
        self.items.append(("zadd", queue, mapping))

    def hset(self, name, key, value):
        self.items.append(("hset", name, key, value))

    def expire(self, name, seconds):
        self.items.append(("expire", name, seconds))

    def execute(self):
        self.executed = True
        results = []
        for item in self.items:
            cmd = item[0]
            if cmd == "sadd":
                _name, value = item[1], item[2]
                results.append(self.client.sadd(_name, value))
            elif cmd == "zadd":
                _queue, mapping = item[1], item[2]
                self.client.queue_store.update(mapping)
                results.append(1)
            elif cmd == "hset":
                _name, key, value = item[1], item[2], item[3]
                self.client.payload_store[key] = value
                results.append(1)
            elif cmd == "expire":
                results.append(True)
            else:
                results.append(1)
        self.items.clear()
        return results


class _FakeLock:
    def __init__(self, acquire=True, owned=True):
        self.acquire_result = acquire
        self.owned_result = owned
        self.released = False

    def acquire(self):
        return self.acquire_result

    def owned(self):
        return self.owned_result

    def release(self):
        self.released = True


class _FakeRedisClient:
    def __init__(self):
        self.seen = set()
        self.queue_store: dict[str, float] = {}
        self.payload_store: dict[str, str] = {}
        self.pipe = _FakePipeline(self)
        self.last_pipe: _FakePipeline | None = None
        self.pushed_executed = False
        self.rows = []
        self.lock_value = _FakeLock()

    def pipeline(self, transaction=True):
        pipe = _FakePipeline(self)
        self.last_pipe = pipe
        self.pushed_executed = True
        return pipe

    def sadd(self, _name, value):
        if value in self.seen:
            return 0
        self.seen.add(value)
        return 1

    def zpopmin(self, _queue, _count):
        rows, self.rows = self.rows, []
        return rows

    def hget(self, _name, key):
        return self.payload_store.get(key)

    def hdel(self, _name, key):
        return self.payload_store.pop(key, None)

    def zcard(self, _queue):
        return len(self.queue_store)

    def lock(self, *_args, **_kwargs):
        return self.lock_value


def test_redis_frontier_push_pop_size_and_lock(monkeypatch) -> None:
    client = _FakeRedisClient()

    class RedisFactory:
        @staticmethod
        def from_url(_url, decode_responses):
            assert decode_responses is True
            return client

    monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=RedisFactory))
    frontier = RedisFrontier("redis://example", "tests")
    request = CrawlRequest("https://example.org", priority=7, meta={"root_url": "https://example.org"})
    assert frontier.push([request, request]) == 1
    assert client.pushed_executed, "push 应通过 pipeline 批量提交"
    assert frontier.size() == 1
    assert frontier.pop() is None

    # 队列成员是 fingerprint，payload 从 hash 还原
    fingerprint = next(iter(client.queue_store))
    client.rows = [(fingerprint, -7.0)]
    popped = frontier.pop()
    assert popped is not None and popped.url == request.url and popped.priority == 7
    assert fingerprint not in client.payload_store  # pop 后 payload 已清理

    lock = frontier.acquire_lock("task", timeout_seconds=10, blocking_timeout_seconds=1)
    assert lock is client.lock_value
    RedisFrontier.release_lock(lock)
    assert client.lock_value.released
    RedisFrontier.release_lock(None)

    client.lock_value = _FakeLock(acquire=False)
    assert frontier.acquire_lock("busy") is None


def test_redis_frontier_push_accepts_generator_and_dedups_by_fingerprint(monkeypatch) -> None:
    client = _FakeRedisClient()

    class RedisFactory:
        @staticmethod
        def from_url(_url, decode_responses):
            return client

    monkeypatch.setitem(sys.modules, "redis", SimpleNamespace(Redis=RedisFactory))
    frontier = RedisFrontier("redis://example", "tests")
    request = CrawlRequest("https://example.org")

    def gen():
        yield request
        yield CrawlRequest("https://other.example")
        yield request  # 重复指纹

    assert frontier.push(gen()) == 2  # S1.5.5：生成器输入计数正确，去重后入队 2 个
    assert frontier.size() == 2


def test_scrapy_bridge_success_failure_and_validation(tmp_path: Path) -> None:
    config = _config(tmp_path, source={"kind": "scrapy"})
    with pytest.raises(ValueError):
        run_scrapy(config)

    spider = tmp_path / "spider.py"
    spider.write_text("class Demo: pass\n", encoding="utf-8")
    config = _config(
        tmp_path,
        source={"kind": "scrapy", "spider_file": str(spider), "arguments": {"topic": "policy"}},
    )
    completed = SimpleNamespace(returncode=0, stdout="ok", stderr="")
    with patch("subprocess.run", return_value=completed) as run:
        summary = run_scrapy(config)
    assert summary["status"] == "succeeded"
    assert "topic=policy" in run.call_args.args[0]

    completed = SimpleNamespace(returncode=2, stdout="", stderr="failed")
    with patch("subprocess.run", return_value=completed):
        with pytest.raises(RuntimeError, match="Scrapy"):
            run_scrapy(config)

    missing = _config(tmp_path, source={"kind": "scrapy", "spider_file": "missing.py"})
    with pytest.raises(FileNotFoundError):
        run_scrapy(missing)


class _AsyncResponse:
    def __init__(self, url, status, headers=None, chunks=None):
        self.url = url
        self.status_code = status
        self.headers = headers or {}
        self._chunks = chunks or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def raise_for_status(self):
        return None

    async def aiter_bytes(self):
        for chunk in self._chunks:
            yield chunk


class _AsyncClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return next(self.responses)


def test_async_fetcher_redirect_method_and_size_guards(tmp_path: Path) -> None:
    pytest.importorskip("httpx")
    config = _config(tmp_path)
    fetcher = HTTPXAsyncFetcher(config)
    client = _AsyncClient(
        [
            _AsyncResponse(
                "https://example.org/start", 302, {"location": "/final"}
            ),
            _AsyncResponse(
                "https://example.org/final", 200, {"content-type": "text/plain"}, [b"ok"]
            ),
        ]
    )
    request = CrawlRequest("https://example.org/start", method="POST", body=b"payload")
    result = asyncio.run(fetcher._request(client, request))
    assert result.final_url == "https://example.org/final" and result.body == b"ok"
    assert [call[:2] for call in client.calls] == [
        ("POST", "https://example.org/start"),
        ("GET", "https://example.org/final"),
    ]

    declared = _AsyncClient(
        [_AsyncResponse("https://example.org", 200, {"content-length": "2048"})]
    )
    with pytest.raises(ResponseTooLargeError):
        asyncio.run(fetcher._request(declared, CrawlRequest("https://example.org")))

    streamed = _AsyncClient(
        [_AsyncResponse("https://example.org", 200, {}, [b"x" * 800, b"y" * 800])]
    )
    with pytest.raises(ResponseTooLargeError):
        asyncio.run(fetcher._request(streamed, CrawlRequest("https://example.org")))

    no_redirects = HTTPXAsyncFetcher(_config(tmp_path, http={"max_redirects": 0}))
    redirected = _AsyncClient(
        [_AsyncResponse("https://example.org", 302, {"location": "/again"})]
    )
    with pytest.raises(PermanentFetchError, match="重定向"):
        asyncio.run(no_redirects._request(redirected, CrawlRequest("https://example.org")))
