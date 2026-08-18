"""TLS 指纹伪装抓取器（可选依赖 curl_cffi）— 补齐反检测协议层。

对齐 Helios 反检测第一层：模拟真实浏览器的 TLS/HTTP2 指纹（JA3/JA4、
ALPN、Header 顺序），对抗基于 TLS 握手的服务器端检测。

安全边界（与 httpx 主链路一致）：
  - 连接前经 NetworkTargetPolicy.approved_addresses 解析并只连已批准地址字面量
    （curl_cffi ``resolve`` 覆盖 DNS，消除 TOCTOU 窗口，S1.3.5）。
  - 请求经 EgressBroker.request 授权与并发预算，响应经 record_response 计费审计。
  - curl_cffi 未安装或初始化失败时回退到 httpx 主链路，绝不阻断采集。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from ..core.config import AppConfig
from ..core.errors import ResponseTooLargeError
from ..core.models import CrawlRequest, FetchResult
from ..core.utils import user_agent
from ..security.egress import EgressBroker
from ..security.policy import NetworkTargetPolicy

if TYPE_CHECKING:
    from .async_fetcher import HTTPXAsyncFetcher

LOGGER = logging.getLogger(__name__)

# 可用 impersonate 目标（按浏览器版本从新到旧，配置可覆盖）
DEFAULT_IMPERSONATE = "chrome131"
_IMPERSONATE_CHAIN = ("chrome131", "chrome130", "chrome124", "chrome120", "safari17_0", "edge101")


def _bracket_ipv6(address: str) -> str:
    """curl ``--resolve`` 的 IPv6 地址字面量需要 ``[...]`` 方括号（B13-005）。"""
    if ":" in address and not address.startswith("["):
        return f"[{address}]"
    return address


def _choose_impersonate(preferred: str) -> str:
    from curl_cffi import BrowserType

    available = {item.name for item in BrowserType}
    for candidate in (preferred, *_IMPERSONATE_CHAIN):
        if candidate in available:
            return candidate
    raise RuntimeError(f"curl_cffi 无可用浏览器指纹目标: {preferred}")


@dataclass(slots=True)
class TLSImpersonator:
    """curl_cffi 驱动的 TLS 指纹伪装抓取器。

    Args:
        config: 应用配置（http 段读取 user_agent/proxy/verify_tls/impersonate）。
        impersonate: 浏览器指纹目标（如 chrome131）；未安装 curl_cffi 时忽略。
        fallback: curl_cffi 不可用时回退的 httpx 抓取器（必须提供）。
        egress: 出口审计（缺省自动构建）。
    """

    config: AppConfig
    fallback: HTTPXAsyncFetcher
    impersonate: str = DEFAULT_IMPERSONATE
    egress: EgressBroker | None = None

    target_policy: NetworkTargetPolicy = field(init=False, repr=False)
    _egress: EgressBroker = field(init=False, repr=False)
    _available: bool = field(init=False, repr=False)
    _resolved_target: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.target_policy = NetworkTargetPolicy(self.config)
        self._egress = self.egress or EgressBroker(self.config, policy=self.target_policy)
        self._available = False
        self._resolved_target: str = ""
        try:
            import curl_cffi  # noqa: F401
        except ImportError:
            LOGGER.info("curl_cffi 未安装，TLS 指纹伪装不可用，回退 httpx 主链路")
            return
        try:
            self._resolved_target = _choose_impersonate(self.impersonate)
            self._available = True
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("TLS 指纹目标初始化失败，回退 httpx: %s", exc)

    @property
    def available(self) -> bool:
        return self._available

    async def fetch_async(self, request: CrawlRequest) -> FetchResult:
        """异步抓取（curl_cffi 不可用时委托 fallback）。"""
        if not self._available:
            results = await self.fallback.fetch_many([request])
            result = results[0]
            if isinstance(result, Exception):
                raise result
            return result
        return await self._impersonate_fetch(request)

    def fetch(self, request: CrawlRequest) -> FetchResult:
        """同步抓取（curl_cffi 不可用时委托 fallback）。"""
        if not self._available:
            return self.fallback.fetch(request)
        import asyncio

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self._impersonate_fetch(request))
        finally:
            loop.close()

    # ── 内部实现 ──────────────────────────────────────────────────────

    def _resolve_override(self, url: str) -> list[bytes] | None:
        """把 host 解析并钉扎为已批准地址字面量（S1.3.5 DNS 重绑定防护）。

        返回 curl ``--resolve`` 格式条目（bytes: "host:port:address"）；
        无可用地址（DNS 未解析出结果）时返回 None，由 curl 自行解析。
        IPv6 地址带 ``[...]`` 括号（curl --resolve 语义），测试可确定性拆分。
        """
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port or (443 if parts.scheme == "https" else 80)
        approved = self.target_policy.approved_addresses(host, port)
        if not approved:
            return None
        return [
            f"{host}:{port}:{_bracket_ipv6(address)}".encode("ascii", errors="replace")
            for address in approved
        ]

    async def _impersonate_fetch(self, request: CrawlRequest) -> FetchResult:
        from curl_cffi import CurlOpt
        from curl_cffi.requests import AsyncSession

        http = self.config.section("http")
        maximum = int(http.get("max_response_bytes", 50_000_000))
        timeout = float(http.get("timeout_seconds", 25))
        headers = {
            "User-Agent": str(http.get("user_agent", user_agent())),
            **http.get("headers", {}), **request.headers,
        }
        proxy = str(http.get("proxy")) or None
        if proxy:
            self.target_policy.require(proxy)
        resolve = self._resolve_override(request.url)
        session_options: dict[str, Any] = {"impersonate": self._resolved_target}
        if resolve:
            session_options["curl_options"] = {CurlOpt.RESOLVE: resolve}
        start = __import__("time").monotonic()

        async with AsyncSession(**session_options) as session:
            with self._egress.request(request.url, purpose="fetch", headers=headers):
                response = await session.get(
                    request.url,
                    headers=headers,
                    timeout=timeout,
                    # B03-009：显式不跟随重定向——重定向目标必须重新过 egress 策略
                    #（出口策略按最终目标授权，跟随会绕过目标校验）。
                    allow_redirects=False,
                    verify=bool(http.get("verify_tls", True)),
                    proxy=proxy or None,
                )
                body = response.content
                if len(body) > maximum:
                    raise ResponseTooLargeError(f"响应超过大小限制: > {maximum}")
                final_url = str(getattr(response, "url", "") or request.url)
                self._egress.record_response(len(body), url=final_url)
                return FetchResult(
                    request=request,
                    final_url=final_url,
                    status=response.status_code,
                    headers=dict(response.headers),
                    body=bytes(body),
                    elapsed_seconds=__import__("time").monotonic() - start,
                )
