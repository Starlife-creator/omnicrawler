from __future__ import annotations

import asyncio
import logging
import time
import urllib.parse
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

from ..core.config import AppConfig
from ..core.errors import PermanentFetchError, ResponseTooLargeError
from ..core.models import CrawlRequest, FetchResult
from ..core.utils import user_agent
from ..security.egress import EgressBroker
from ..security.policy import HostRateLimiter, NetworkTargetPolicy
from .retry import backoff_seconds, parse_retry_config, retry_after_seconds

LOGGER = logging.getLogger(__name__)


class _PinnedAsyncNetworkBackend:
    """Duck-typed httpcore network backend that pins every socket to a
    policy-approved address literal (S1.3.5 DNS 重绑定防护)。

    连接目标前经 ``approved_addresses`` 单次解析并校验，之后只连返回的
    地址字面量，不再二次解析主机名（消除 TOCTOU 窗口）。
    """

    def __init__(self, inner: Any, target_policy: NetworkTargetPolicy) -> None:
        self._inner = inner
        self._policy = target_policy

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Any = None,
    ) -> Any:
        addresses = self._policy.approved_addresses(host, port)
        last_error: OSError | None = None
        for address in addresses:
            try:
                return await self._inner.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except OSError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise OSError(f"没有可连接的已批准地址: {host}:{port}")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Any = None,
    ) -> Any:
        return await self._inner.connect_unix_socket(path, timeout=timeout, socket_options=socket_options)

    async def sleep(self, seconds: float) -> None:
        return await self._inner.sleep(seconds)


class HTTPXAsyncFetcher:
    """HTTPX异步抓取器；可由Pipeline选择，也可在插件中批量调用fetch_many。"""

    def __init__(self, config: AppConfig, limiter=None, egress: EgressBroker | None = None) -> None:
        self.config = config
        delay = float(config.section("http").get("delay_seconds", 1))
        # S2.5.48：单请求与批量请求共用同一限速器实例（按主机），
        # 消除单/批并发下实际速率翻倍
        self.limiter = limiter or HostRateLimiter(delay)
        self.target_policy = NetworkTargetPolicy(config)
        self.egress = egress or EgressBroker(config, policy=self.target_policy)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: httpx.AsyncClient | None = None
        # 跨事件循环缓存：插件线程各自的 loop 各自持有客户端（S1.5.8）
        self._loop_clients: dict[int, tuple[asyncio.AbstractEventLoop, Any]] = {}

    def _build_client(self) -> Any:
        """Build a fresh httpx AsyncClient wired to the current policy."""
        import httpx

        http = self.config.section("http")
        limits = httpx.Limits(max_connections=int(self.config.section("crawl").get("concurrency", 4)))
        headers = {
            "User-Agent": str(http.get("user_agent", user_agent())),
            **http.get("headers", {}), **self.config.section("source").get("headers", {}),
        }
        proxy = str(http.get("proxy")) or None
        if proxy:
            self.target_policy.require(proxy)
        transport = httpx.AsyncHTTPTransport(
            verify=bool(http.get("verify_tls", True)),
            proxy=proxy,
            limits=limits,
        )
        try:
            self._pin_transport_dns(transport)
        except Exception as exc:  # 传输内部结构变化时回退默认行为，不阻断抓取
            LOGGER.warning("无法启用异步 DNS 固定，回退默认传输: %s", exc)
        return httpx.AsyncClient(
            headers=headers,
            timeout=float(http.get("timeout_seconds", 25)),
            follow_redirects=False,
            transport=transport,
        )

    def _client_for(self, loop: asyncio.AbstractEventLoop) -> Any:
        """Return an httpx client bound to *loop*, creating per-loop instances as needed.

        不强制 set_event_loop：客户端与其事件循环一一绑定，跨循环复用同一
        客户端会触发 "Future attached to a different loop"（S1.5.8）。
        """
        if loop is self._loop and self._client is not None and not loop.is_closed():
            return self._client
        entry = self._loop_clients.get(id(loop))
        if entry is not None and not entry[0].is_closed():
            return entry[1]
        client = self._build_client()
        self._loop_clients[id(loop)] = (loop, client)
        return client

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Lazily create and reuse a persistent event loop for the sync ``fetch`` path."""
        if self._loop is None or self._loop.is_closed():
            self._loop = asyncio.new_event_loop()
            self._client = self._build_client()
            self._loop_clients[id(self._loop)] = (self._loop, self._client)
        return self._loop

    def _pin_transport_dns(self, transport: Any) -> None:
        """把传输底层连接池的网络后端替换为 DNS 固定后端（S1.3.5）。"""
        import httpcore

        pool = getattr(transport, "_pool", None)
        if pool is None or not hasattr(pool, "_network_backend"):
            return
        pool._network_backend = _PinnedAsyncNetworkBackend(
            httpcore.AsyncBackend(), self.target_policy
        )

    def close(self) -> None:
        """Clean up all per-loop clients and the persistent event loop."""
        for loop, client in list(self._loop_clients.values()):
            if loop is not None and not loop.is_closed() and client is not None:
                try:
                    loop.run_until_complete(client.aclose())
                except RuntimeError:
                    # loop 正在运行（如被插件关闭）时无法 run_until_complete，忽略即可
                    pass
        self._loop_clients.clear()
        self._client = None
        if self._loop is not None and not self._loop.is_closed():
            self._loop.close()
            self._loop = None

    def fetch(self, request: CrawlRequest) -> FetchResult:
        loop = self._ensure_loop()
        return loop.run_until_complete(self._fetch_one(request))

    async def fetch_many(self, requests: list[CrawlRequest]) -> list[FetchResult | Exception]:
        running_loop = asyncio.get_running_loop()
        client = self._client_for(running_loop)
        semaphore = asyncio.Semaphore(int(self.config.section("crawl").get("concurrency", 4)))

        async def guarded(request: CrawlRequest):
            try:
                async with semaphore:
                    # S2.5.48：单/批统一同一限速器（to_thread 桥接同步等待）
                    await asyncio.to_thread(self.limiter.wait, request.url)
                    return await self._request(client, request)
            except Exception as exc:  # Per-request result, not batch cancellation.
                return exc

        return list(await asyncio.gather(*(guarded(request) for request in requests)))

    async def _fetch_one(self, request: CrawlRequest) -> FetchResult:
        results = await self.fetch_many([request])
        result = results[0]
        if isinstance(result, Exception):
            raise result
        return result

    async def _request(self, client, request: CrawlRequest) -> FetchResult:
        import httpx

        http = self.config.section("http")
        # S2.1.4：retries 为总尝试次数（0 表示不重试）；retry_on_status 配置驱动
        retries = max(1, int(http.get("retries", 3)))
        retry_cfg = parse_retry_config(http)
        retry_statuses = retry_cfg["status_codes"]
        maximum = int(http.get("max_response_bytes", 50_000_000))
        max_redirects = int(http.get("max_redirects", 10))
        last: Exception | None = None
        for attempt in range(retries):
            started = time.monotonic()
            try:
                current_url = request.url
                method = request.method
                content = request.body
                for redirect_count in range(max_redirects + 1):
                    purpose = "fetch" if redirect_count == 0 else "redirect"
                    with self.egress.request(current_url, purpose=purpose, headers=request.headers):
                        async with client.stream(
                            method, current_url, headers=request.headers, content=content,
                        ) as response:
                            if response.status_code in {301, 302, 303, 307, 308} and response.headers.get("location"):
                                if redirect_count >= max_redirects:
                                    raise PermanentFetchError(f"重定向次数超过上限: {max_redirects}")
                                current_url = urllib.parse.urljoin(str(response.url), response.headers["location"])
                                if response.status_code == 303 or (
                                    response.status_code in {301, 302} and method.upper() not in {"GET", "HEAD"}
                                ):
                                    method, content = "GET", None
                                continue
                            if response.status_code in retry_statuses:
                                response.raise_for_status()
                            response.raise_for_status()
                            declared = response.headers.get("content-length", "")
                            if declared.isdigit() and int(declared) > maximum:
                                raise ResponseTooLargeError(f"响应超过大小限制: {declared} > {maximum}")
                            chunks: list[bytes] = []
                            size = 0
                            async for chunk in response.aiter_bytes():
                                size += len(chunk)
                                if size > maximum:
                                    raise ResponseTooLargeError(f"响应超过大小限制: > {maximum}")
                                chunks.append(chunk)
                            self.egress.record_response(size, url=str(response.url))
                            self.egress.record_success(str(response.url))
                            return FetchResult(
                                request, str(response.url), response.status_code,
                                {key.lower(): value for key, value in response.headers.items()},
                                b"".join(chunks), time.monotonic() - started,
                            )
                raise PermanentFetchError("重定向处理未得到最终响应")
            except (ResponseTooLargeError, PermanentFetchError):
                raise
            except httpx.HTTPStatusError as exc:
                last = exc
                if exc.response.status_code not in retry_statuses or attempt + 1 >= retries:
                    raise PermanentFetchError(
                        f"HTTP {exc.response.status_code}: {exc.request.url}"
                    ) from exc
                self.egress.record_failure(current_url, error=f"HTTP {exc.response.status_code}")
                wait = retry_after_seconds(exc.response.headers)
                if wait is None:
                    wait = backoff_seconds(
                        attempt,
                        base=retry_cfg["base_seconds"],
                        maximum=retry_cfg["max_seconds"],
                        jitter=retry_cfg["jitter"],
                    )
                else:
                    # S2.5.9：Retry-After 封顶（默认 60s），超限不再静默长睡
                    cap = float(http.get("retry_after_cap_seconds", 60))
                    if wait > cap:
                        LOGGER.warning(
                            "服务端 Retry-After=%ss 超过封顶 %ss，按 %ss 等待", wait, cap, cap
                        )
                        wait = cap
                await asyncio.sleep(wait)
            except httpx.TransportError as exc:
                last = exc
                self.egress.record_failure(current_url, error=str(exc))
                if attempt + 1 >= retries:
                    raise
                await asyncio.sleep(backoff_seconds(
                    attempt,
                    base=retry_cfg["base_seconds"],
                    maximum=retry_cfg["max_seconds"],
                    jitter=retry_cfg["jitter"],
                ))
        assert last is not None
        raise last


def register(registry) -> None:
    registry.register_fetcher("httpx_async", HTTPXAsyncFetcher)
