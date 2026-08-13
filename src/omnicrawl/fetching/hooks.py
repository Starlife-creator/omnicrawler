"""P2-3：抓取三阶段钩子注册表（before_fetch / after_fetch / on_error）。

借鉴 Colly hook 点「请求前 / 响应后 / 错误时」的解耦思想：
    * 钩子按注册顺序同步执行（Callable → 可 async）
    * 单钩子异常隔离 + 记录日志，不影响请求主流程与其他钩子
    * 严格只读约束：允许观测 / 记录 / 设置 request.meta（自定义标签），
      禁止改变请求 URL / method / body / headers（防止隐性篡改产生合规风险）

使用：
    hooks = FetchHooks()
    hooks.on_before_fetch(lambda req, ctx: print(f"GET {req.url}"))
    hooks.on_after_fetch(lambda req, result, ctx: metrics.incr(result.status_code))
    hooks.on_error(lambda req, exc, ctx: logger.warning(exc))

    # 在抓取引擎中
    ctx: dict = {}
    req = await hooks.emit_before_fetch(req, ctx)
    try:
        result = await do_fetch(req)
        hooks.emit_after_fetch(req, result, ctx)
    except Exception as exc:
        hooks.emit_error(req, exc, ctx)
        raise
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from ..core.models import CrawlRequest, FetchResult

LOGGER = logging.getLogger(__name__)

__all__ = ["FetchHooks", "BeforeHook", "AfterHook", "ErrorHook"]

BeforeHook = Callable[[CrawlRequest, dict[str, Any]], None | Awaitable[None]]
AfterHook = Callable[[CrawlRequest, FetchResult, dict[str, Any]], None | Awaitable[None]]
ErrorHook = Callable[[CrawlRequest, Exception, dict[str, Any]], None | Awaitable[None]]


@dataclass(slots=True)
class FetchHooks:
    """轻量钩子注册表：三阶段 + 异常隔离 + 支持 async/sync 两类钩子。"""

    _before: list[BeforeHook] = field(default_factory=list)
    _after: list[AfterHook] = field(default_factory=list)
    _error: list[ErrorHook] = field(default_factory=list)

    # ── 注册 ──────────────────────────────────────────────
    def on_before_fetch(self, hook: BeforeHook) -> BeforeHook:
        """注册 before_fetch 钩子（装饰器友好：返回原 hook）。"""
        self._before.append(hook)
        return hook

    def on_after_fetch(self, hook: AfterHook) -> AfterHook:
        self._after.append(hook)
        return hook

    def on_error(self, hook: ErrorHook) -> ErrorHook:
        self._error.append(hook)
        return hook

    # ── 发射 ──────────────────────────────────────────────
    async def emit_before_fetch(self, request: CrawlRequest, ctx: dict[str, Any]) -> CrawlRequest:
        """按顺序触发 before 钩子。返回 request（便于未来扩展 request 变更检测）。

        异常策略：单钩子异常只记录日志，绝不阻断主流程；request 对象按引用传递，
        钩子可修改 meta（合规观测标签），但 **禁止修改 url/method/body/headers**（未来
        加 assert 保护）。
        """
        for hook in list(self._before):
            try:
                result = hook(request, ctx)
                if result is not None and asyncio.iscoroutine(result):
                    await result
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("before_fetch hook 执行失败，跳过: %s (%s)", hook, exc)
        return request

    async def emit_after_fetch(
        self, request: CrawlRequest, result: FetchResult, ctx: dict[str, Any]
    ) -> None:
        for hook in list(self._after):
            try:
                r = hook(request, result, ctx)
                if r is not None and asyncio.iscoroutine(r):
                    await r
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("after_fetch hook 执行失败，跳过: %s (%s)", hook, exc)

    async def emit_error(
        self, request: CrawlRequest, exc: Exception, ctx: dict[str, Any]
    ) -> None:
        for hook in list(self._error):
            try:
                r = hook(request, exc, ctx)
                if r is not None and asyncio.iscoroutine(r):
                    await r
            except Exception as hook_exc:  # noqa: BLE001
                LOGGER.warning(
                    "on_error hook 执行失败，跳过: %s (%s)", hook, hook_exc
                )

    # ── 合并：方便给线程级 fetcher 组合多个 hook 源 ─────
    def extend(self, other: FetchHooks) -> None:
        self._before.extend(other._before)
        self._after.extend(other._after)
        self._error.extend(other._error)

    def is_empty(self) -> bool:
        return not (self._before or self._after or self._error)
