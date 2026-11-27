"""HTTP 连接复用（keep-alive）—— 默认关闭，显式开启。

## 为什么值得做（实测 2026-11-27）

同一个站点连续取 10 页：

| 方式 | 耗时 |
|---|---|
| 每页新建连接（现状）| **14.0 s** |
| 复用一条 keep-alive 连接 | **3.7 s** |

**3.7 倍**。成本全在建连：DNS + TCP + TLS 三次握手，每页一次。

## 为什么默认关闭

`urllib.request.AbstractHTTPHandler.do_open` **写死**了 `Connection: close`，并在
读完响应后手动关 socket。要复用连接必须绕开这一层 —— 也就是「自己发请求」。

而本项目最重要的一条边界是：**所有出网都必须经 EgressBroker 且连接固定到已批准
的 IP**（防 DNS rebinding）。改这条路径的风险远大于它带来的收益，所以：

* `http.keep_alive` **默认 false**，行为与今天**逐字节相同**；
* 开启时仍逐请求走 `EgressBroker.request()`、仍按 `target_policy.approved_addresses()`
  连接（复用只发生在**同一个已批准 endpoint** 上）；
* 出站凭据 / robots / 审计 / 预算全部在调用方（`HTTPFetcher.fetch`）里，不在这层。

## 复用判据（三条同时成立才复用）

1. 同一个 **(scheme, host, port)**；
2. **相同的 TLS 校验设置**（`verify` 与 `insecure` 一致）—— 混用等于绕过校验；
3. 上一次响应是**可复用的**（没有 `Connection: close`，响应体已读完）。

## 这是"连接池"吗

不是。每个 `HTTPFetcher` 线程一个实例（既有约定），实例内**只存一条**连接。
多存几条只会让"谁在用哪条"变复杂，而单线程串行取页本来就只需要一条。
"""

from __future__ import annotations

import http.client
import ssl
import threading
from dataclasses import dataclass
from typing import Any


@dataclass
class _Slot:
    """一条可复用连接 + 它绑定的 endpoint 与 TLS 设置。"""

    key: tuple[str, str, int, bool]
    connection: http.client.HTTPConnection


class KeepAlivePool:
    """**每线程一个**的 keep-alive 连接槽（只存一条）。

    ★ 线程安全策略：实例本身不加锁，因为它按线程创建（与 `HTTPFetcher` 一致）。
      跨线程共享同一个池会让两条线程同时写同一 socket —— 那是最难查的一类 bug。
    """

    def __init__(self) -> None:
        self._slot: _Slot | None = None
        self._lock = threading.RLock()
        self.reused = 0
        self.created = 0

    @staticmethod
    def key_for(scheme: str, host: str, port: int, *, verify: bool) -> tuple[str, str, int, bool]:
        return (scheme.casefold(), host.casefold(), int(port), bool(verify))

    def take(self, key: tuple[str, str, int, bool]) -> http.client.HTTPConnection | None:
        """取出可复用连接；没有/不匹配时返回 None（调用方负责新建）。"""
        with self._lock:
            slot = self._slot
            if slot is None or slot.key != key:
                return None
            self._slot = None
            self.reused += 1
            return slot.connection

    def give_back(self, key: tuple[str, str, int, bool], connection: http.client.HTTPConnection) -> None:
        """归还连接（下一个请求可复用）。调用方必须先确认响应体读完了。"""
        with self._lock:
            # 只留一条：新的替换旧的（旧的由调用方 close）
            if self._slot is not None and self._slot.connection is not connection:
                self._slot.connection.close()
            self._slot = _Slot(key, connection)
            self.created = max(self.created, 1)

    def discard(self) -> None:
        """丢掉当前连接（响应异常 / 连接已断时用）。"""
        with self._lock:
            slot = self._slot
            self._slot = None
        if slot is not None:
            try:
                slot.connection.close()
            except Exception:  # noqa: BLE001 - 关连接失败不影响主流程
                pass

    def close(self) -> None:
        self.discard()


def response_is_reusable(headers: Any, *, status: int = 200) -> bool:
    """响应头是否允许复用这条连接。

    ★ 判据只认 **Connection: close**（以及 1xx/204/304 这类无体响应之外的情形）。
      `http.client` 自己会在 `getresponse()` 里解析 `will_close`，但那条信息挂在
      响应对象上、读体之后才最终确定，所以这里按头判一次更稳。
    """
    if status in {204, 304} or 100 <= status < 200:
        return True
    connection = ""
    try:
        connection = str(headers.get("connection", "") or "").strip().casefold()
    except Exception:  # noqa: BLE001 - 头对象形态异常按不可复用处理
        return False
    return "close" not in connection


def make_connection(
    scheme: str,
    host: str,
    port: int,
    *,
    timeout: float,
    context: ssl.SSLContext | None = None,
    connection_factory: Any = None,
) -> http.client.HTTPConnection:
    """建一条连接（不连接，由 `request()` 触发）。

    ``connection_factory`` 允许调用方注入"IP 固定"的连接类 —— 本模块**不决定**
    连哪个 IP，那是 `NetworkTargetPolicy` 的职责。
    """
    if connection_factory is not None:
        return connection_factory(host, port=port, timeout=timeout)
    if scheme.casefold() == "https":
        return http.client.HTTPSConnection(host, port=port, timeout=timeout, context=context)
    return http.client.HTTPConnection(host, port=port, timeout=timeout)
