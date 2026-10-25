"""A-P2-8：代理池选择 / 粘性绑定 / 失败摘除 / 健康回退（纯逻辑组件）。

本模块只承载代理选择的确定性状态机，不发起任何网络请求：

- **选择**：优先复用域名粘性绑定；绑定失效后在健康代理间轮询；
  全部摘除冷却中时返回 ``None``（调用方按无代理直连处理，由上层 egress 策略把关）。
- **粘性绑定**：同一域名优先走同一代理，降低会话/风控状态漂移；代理失败时解除
  指向它的全部粘性绑定。
- **失败摘除**：连续失败达到 ``max_failures`` 即摘除，进入 ``cooldown_seconds``
  冷却；冷却结束后重新纳入候选（半开探测由下一次成功/失败报告裁决）。
- **成功恢复**：成功调用清零失败计数并恢复健康状态。

⚠ 网络接入状态：本池已与纯逻辑单测闭环；把多代理接入 ``urllib`` opener /
异步客户端的实际切换、代理连通性探测与超时回退，待对应网络环境集成验证
（环境受限，不在本批次伪编译、伪测试）。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ProxyEntry:
    url: str
    healthy: bool = True
    consecutive_failures: int = 0
    cooldown_until: float = 0.0


@dataclass(slots=True)
class ProxyPool:
    """线程安全的代理池（纯状态机，无网络 I/O）。"""

    proxies: list[ProxyEntry]
    cooldown_seconds: float = 30.0
    max_failures: int = 3
    _sticky: dict[str, str] = field(default_factory=dict, repr=False)
    _rr_index: int = field(default=0, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @classmethod
    def from_urls(
        cls,
        urls: list[str],
        *,
        cooldown_seconds: float = 30.0,
        max_failures: int = 3,
    ) -> "ProxyPool":
        entries = [ProxyEntry(url=url) for url in dict.fromkeys(str(url).strip() for url in urls if str(url).strip())]
        return cls(proxies=entries, cooldown_seconds=cooldown_seconds, max_failures=max_failures)

    # ── 内部 ────────────────────────────────────────────────────────

    def _eligible(self, now: float) -> list[ProxyEntry]:
        # 健康代理直接可选；摘除代理在冷却结束后重新纳入候选（半开探测由下次报告裁决）。
        return [
            entry for entry in self.proxies
            if entry.healthy or now >= entry.cooldown_until
        ]

    def _find(self, url: str) -> ProxyEntry | None:
        for entry in self.proxies:
            if entry.url == url:
                return entry
        return None

    # ── 选择 ────────────────────────────────────────────────────────

    def select(self, domain: str) -> str | None:
        """为域名选择代理 URL；无可用代理时返回 ``None``。"""
        now = time.monotonic()
        with self._lock:
            bound = self._sticky.get(domain)
            if bound is not None:
                entry = self._find(bound)
                if entry is not None and (entry.healthy or now >= entry.cooldown_until):
                    return bound
                # 绑定代理已摘除：解除粘性，重新选择
                self._sticky.pop(domain, None)

            eligible = self._eligible(now)
            if not eligible:
                return None
            entry = eligible[self._rr_index % len(eligible)]
            self._rr_index = (self._rr_index + 1) % len(self.proxies)
            self._sticky[domain] = entry.url
            return entry.url

    # ── 健康报告 ──────────────────────────────────────────────────────

    def report_success(self, proxy_url: str) -> None:
        with self._lock:
            entry = self._find(proxy_url)
            if entry is None:
                return
            entry.consecutive_failures = 0
            entry.healthy = True
            entry.cooldown_until = 0.0

    def report_failure(self, proxy_url: str) -> None:
        """记录一次失败；达到阈值摘除并解除指向该代理的粘性绑定。"""
        now = time.monotonic()
        with self._lock:
            entry = self._find(proxy_url)
            if entry is None:
                return
            entry.consecutive_failures += 1
            if entry.consecutive_failures >= self.max_failures:
                entry.healthy = False
                entry.cooldown_until = now + self.cooldown_seconds
            for domain, bound in list(self._sticky.items()):
                if bound == proxy_url:
                    self._sticky.pop(domain, None)

    def status(self) -> list[dict[str, Any]]:
        """诊断快照（测试与排障读取，不暴露内部锁状态）。"""
        with self._lock:
            return [
                {
                    "url": entry.url,
                    "healthy": entry.healthy,
                    "consecutive_failures": entry.consecutive_failures,
                    "cooldown_until": entry.cooldown_until,
                }
                for entry in self.proxies
            ]
