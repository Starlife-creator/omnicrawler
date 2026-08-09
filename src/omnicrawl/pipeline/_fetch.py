"""Fetch mixin: per-thread fetcher access, checked fetch and artifact storage."""

from __future__ import annotations

import logging
from pathlib import Path

from ..core.models import CrawlRequest, FetchResult
from ..core.utils import safe_filename
from ..fetching.routing import needs_browser
from ..plugins.plugin_runtime import prepare_request
from ._mixin_base import _PipelineBase

LOGGER = logging.getLogger("omnicrawl")


class _PipelineFetch(_PipelineBase):
    def _thread_fetcher(self, name: str):
        built_in = name in {"http", "browser", "httpx_async"}
        if name == "browser":
            with self._shared_fetchers_lock:
                if name not in self._shared_fetchers:
                    self._shared_fetchers[name] = self.registry.fetchers[name](
                        self.config, self.limiter, self.egress
                    )
                return self._shared_fetchers[name]
        fetchers = getattr(self._local, "fetchers", None)
        if fetchers is None:
            fetchers = self._local.fetchers = {}
        if name not in fetchers:
            factory = self.registry.fetchers[name]
            instance = (
                factory(self.config, self.limiter, self.egress)
                if built_in
                else factory(self.config, self.limiter)
            )
            fetchers[name] = instance
            # S2.5.45：登记线程局部 fetcher，Pipeline.close 统一回收防连接泄漏
            with self._all_fetchers_lock:
                self._all_fetchers.append(instance)
        return fetchers[name]

    def _fetch_checked(self, run_id: str, request: CrawlRequest) -> FetchResult:
        # === Stage: Fetch ===
        if self._auth_provider is not None:
            request = prepare_request(self._auth_provider, request)
        self._emit("before_fetch", run_id=run_id, request=request)
        root = request.meta.get("root_url")
        allowed, reason = self.scope.allowed(request.url, str(root) if root else None)
        if not allowed:
            raise PermissionError(reason)
        if not self.robots.allowed(request.url):
            raise PermissionError("robots.txt不允许抓取此地址，或robots检查失败且配置为fail-closed")
        updates = self.config.section("updates")
        if (
            request.method.upper() == "GET"
            and updates.get("enabled", False)
            and updates.get("use_conditional_requests", True)
        ):
            conditional = self.state.conditional_headers(request.url)
            if conditional:
                request = CrawlRequest(
                    url=request.url, method=request.method,
                    headers={**conditional, **request.headers}, body=request.body,
                    kind=request.kind, render=request.render, priority=request.priority,
                    depth=request.depth, parent_url=request.parent_url, meta=request.meta,
                )
        http_engine = str(self.config.section("http").get("engine", "urllib")).lower()
        name = "browser" if request.render else ("httpx_async" if http_engine == "httpx_async" else "http")
        result = self._thread_fetcher(name).fetch(request)
        if result.status == 304:
            self.metrics.record_fetch(result, engine=name, escalated=False)
            self._emit("after_fetch", run_id=run_id, request=request, result=result, engine=name)
            return result
        escalated = False
        escalate, escalation_reason = needs_browser(result)
        if (
            name != "browser"
            and escalate
            and self.config.section("http").get("auto_browser_fallback", True)
        ):
            LOGGER.info("自动切换浏览器模式: %s (%s)", request.url, escalation_reason)
            browser_request = CrawlRequest(
                url=request.url, method=request.method, headers=request.headers, body=request.body,
                kind=request.kind, render=True, priority=request.priority, depth=request.depth,
                parent_url=request.parent_url, meta={**request.meta, "escalated_from": name},
            )
            result = self._thread_fetcher("browser").fetch(browser_request)
            name = "browser"
            escalated = True
        allowed, reason = self.scope.allowed(result.final_url, str(root) if root else None)
        if not allowed:
            raise PermissionError(f"重定向目标被拦截: {reason}")
        self.metrics.record_fetch(result, engine=name, escalated=escalated)
        self._emit("after_fetch", run_id=run_id, request=request, result=result, engine=name)
        return result

    def _save_artifact(self, result: FetchResult) -> Path:
        suffix = Path(result.final_url.split("?", 1)[0]).suffix.lower()
        if result.content_type == "application/pdf" or suffix == ".pdf" or result.body.startswith(b"%PDF-"):
            category = "pdf"
            content_type = "application/pdf"
        elif result.content_type.startswith(("image/", "audio/", "video/")):
            category = "media"
            content_type = result.content_type
        else:
            category = "files"
            content_type = result.content_type
        name = safe_filename(result.final_url, content_type, result.headers.get("content-disposition", ""))
        if category == "pdf" and not name.lower().endswith(".pdf"):
            name += ".pdf"
        named = Path(name)
        name = f"{named.stem}_{result.content_hash[:12]}{named.suffix}"
        stored = self.object_store.put(
            f"artifacts/{category}/{name}",
            result.body,
            content_type=content_type,
        )
        if stored.local_path is None:
            raise RuntimeError("Desktop pipeline requires a local recovery copy for artifacts")
        return stored.local_path
