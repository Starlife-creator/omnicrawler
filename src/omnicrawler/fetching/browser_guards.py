"""浏览器抓取的失败关闭守卫 —— 从 browser_fetcher.py 迁出（P1-3 第二批）。

含：
- ``_Watchdog``：超时后回调 on_timeout 并记录 fired 的 fail-closed 看门狗
- ``_SENSITIVE_HEADER_NAMES`` / ``strip_cross_origin_credentials``：
  跨域重定向时剥离敏感请求头（防凭据随跳转外泄）

仅依赖 stdlib（threading / urllib.parse），可独立单测。
"""
from __future__ import annotations

import logging
import threading
from typing import Any
from urllib.parse import urlsplit

LOGGER = logging.getLogger(__name__)

class _Watchdog:
    """Fail-closed watchdog: call ``on_timeout`` after ``seconds`` if the guard
    block hasn't finished, and record ``fired`` so the caller can raise precisely.

    Selenium BiDi 事件流在个别平台可能静默挂起（导航/actions 不抛异常也不返回），
    若不加看门狗，外层 subprocess 只能干等到超时。超时时 on_timeout（通常
    driver.quit()）会解除挂起的 BiDi 调用，主线程恢复后检查 fired 抛明确错误。
    """

    def __init__(self, seconds: float, on_timeout: Any | None = None) -> None:
        self._seconds = max(1.0, seconds)
        self._on_timeout = on_timeout
        self._timer: threading.Timer | None = None
        self.fired = False

    def __enter__(self) -> _Watchdog:
        self._timer = threading.Timer(self._seconds, self._fire)
        self._timer.daemon = True
        self._timer.start()
        return self

    def __exit__(self, *_exc_info: object) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _fire(self) -> None:
        self.fired = True
        if self._on_timeout is not None:
            try:
                self._on_timeout()
            except Exception:  # noqa: BLE001
                LOGGER.exception("watchdog on_timeout 清理失败")

_SENSITIVE_HEADER_NAMES = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "x-api-key",
    "api-key",
}


def strip_cross_origin_credentials(
    headers: dict[str, str],
    target_url: str,
    request_url: str,
) -> dict[str, str] | None:
    """跨来源请求时剔除认证凭据头，返回脱敏后的 headers（同源返回 None）。

    S1.3.4：浏览器页面加载第三方 CDN / 分析脚本时，Auth/Cookie 不得被广播。
    """
    target_netloc = urlsplit(target_url).netloc.casefold()
    request_netloc = urlsplit(request_url).netloc.casefold()
    if target_netloc and target_netloc == request_netloc:
        return None
    stripped = {
        str(key): str(value)
        for key, value in headers.items()
        if str(key).casefold() not in _SENSITIVE_HEADER_NAMES
        and not str(key).casefold().endswith("-api-key")
    }
    if len(stripped) == len(headers):
        return None
    return stripped
