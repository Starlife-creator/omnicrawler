from __future__ import annotations

import asyncio
import time
import urllib.parse
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

from ..core.config import AppConfig
from ..core.errors import PermanentFetchError, ResponseTooLargeError
from ..core.models import CrawlRequest, FetchResult
from ..core.utils import user_agent
from ..security.egress import EgressBroker
from ..security.policy import AsyncHostRateLimiter, HostRateLimiter, NetworkTargetPolicy
from .retry import RETRYABLE_STATUS, backoff_seconds, retry_after_seconds


class HTTPXAsyncFetcher:
    """HTTPX异步抓取器；可由Pipeline选择，也可在插件中批量调用fetch_many。"""

    def __init__(self, config: AppConfig, limiter=None, egress: EgressBroker | None = None) -> None:
        self.config = config
        delay = float(config.section("http").get("delay_seconds", 1))
        self.limiter = limiter or HostRateLimiter(delay)
        self.async_limiter = AsyncHostRateLimiter(delay)
        self.target_policy = NetworkTargetPolicy(config)
        self.egress = egress or EgressBroker(config, policy=self.target_policy)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._client: httpx.AsyncClient | None = None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Lazily create and reuse a persistent event loop and httpx client."""
        if self._loop is None or self._loop.is_closed():
            try:
                import httpx
            except ImportError as exc:
                raise RuntimeError("缺少HTTPX，请安装 omnicrawl[async]") from exc
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            http = self.config.section("http")
            limits = httpx.Limits(max_connections=int(self.config.section("crawl").get("concurrency", 4)))
            headers = {
                "User-Agent": str(http.get("user_agent", user_agent())),
                **http.get("headers", {}), **self.config.section("source").get("headers", {}),
            }
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=float(http.get("timeout_seconds", 25)),
                verify=bool(http.get("verify_tls", True)),
                proxy=str(http.get("proxy")) or None,
                follow_redirects=False,
                limits=limits,
            )
        return self._loop

    def close(self) -> None:
        """Clean up the persistent event loop and httpx client."""
        if self._loop is not None and not self._loop.is_closed():
            if self._client is not None:
                self._loop.run_until_complete(self._client.aclose())
                self._client = None
            self._loop.close()
            self._loop = None

    def fetch(self, request: CrawlRequest) -> FetchResult:
        loop = self._ensure_loop()
        return loop.run_until_complete(self._fetch_one(request))

    async def fetch_many(self, requests: list[CrawlRequest]) -> list[FetchResult | Exception]:
        self._ensure_loop()
        client = self._client
        semaphore = asyncio.Semaphore(int(self.config.section("crawl").get("concurrency", 4)))

        async def guarded(request: CrawlRequest):
            try:
                async with semaphore:
                    if len(requests) == 1:
                        await asyncio.to_thread(self.limiter.wait, request.url)
                    else:
                        await self.async_limiter.wait(request.url)
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
        retries = max(1, int(http.get("retries", 3)))
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
                            if response.status_code in RETRYABLE_STATUS:
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
                if exc.response.status_code not in RETRYABLE_STATUS or attempt + 1 >= retries:
                    raise PermanentFetchError(
                        f"HTTP {exc.response.status_code}: {exc.request.url}"
                    ) from exc
                self.egress.record_failure(current_url, error=f"HTTP {exc.response.status_code}")
                wait = retry_after_seconds(exc.response.headers)
                if wait is None:
                    wait = backoff_seconds(
                        attempt,
                        base=float(http.get("retry_base_seconds", 1)),
                        maximum=float(http.get("retry_max_seconds", 30)),
                        jitter=float(http.get("retry_jitter", 0.25)),
                    )
                await asyncio.sleep(wait)
            except httpx.TransportError as exc:
                last = exc
                self.egress.record_failure(current_url, error=str(exc))
                if attempt + 1 >= retries:
                    raise
                await asyncio.sleep(backoff_seconds(
                    attempt,
                    base=float(http.get("retry_base_seconds", 1)),
                    maximum=float(http.get("retry_max_seconds", 30)),
                    jitter=float(http.get("retry_jitter", 0.25)),
                ))
        assert last is not None
        raise last


def register(registry) -> None:
    registry.register_fetcher("httpx_async", HTTPXAsyncFetcher)
