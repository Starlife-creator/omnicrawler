"""Playwright 浏览器池 —— 从 browser_fetcher.py 迁出（P1-3 第三批）。

含：
- ``_PoolTask``：单次抓取任务（request / done 事件 / 结果或异常 / discarded 标记）
- ``PlaywrightPool``：常驻 worker 线程池 + 上下文复用 + 跳转守卫 + 响应捕获

``_render`` 经 ``browser_engines.run_actions_for_page`` 执行动作序列（不反向依赖
BrowserFetcher）；``strip_cross_origin_credentials`` 来自 browser_guards。
"""
from __future__ import annotations

import json
import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import AppConfig
from ..core.errors import EgressBudgetExceededError, ResponseTooLargeError
from ..core.models import CrawlRequest, FetchResult
from ..core.safe_data import safe_json_loads
from ..security.egress import EgressBroker
from ..security.policy import NetworkTargetPolicy
from .browser_engines import run_actions_for_page
from .browser_guards import strip_cross_origin_credentials

LOGGER = logging.getLogger(__name__)

@dataclass(slots=True)
class _PoolTask:
    request: CrawlRequest
    done: threading.Event
    result: FetchResult | None = None
    error: BaseException | None = None
    # S2.5.11：fetch 调用方超时后置位，worker 据此跳过渲染或释放 context
    discarded: threading.Event = field(default_factory=threading.Event)

class PlaywrightPool:
    """Thread-safe browser pool; each worker owns one browser and isolated reusable contexts."""

    def __init__(
        self,
        config: AppConfig,
        target_policy: NetworkTargetPolicy,
        size: int,
        egress: EgressBroker | None = None,
    ) -> None:
        self.config = config
        self.target_policy = target_policy
        self.egress = egress or EgressBroker(config, policy=target_policy)
        self._queues: list[queue.Queue[_PoolTask | None]] = [queue.Queue() for _ in range(size)]
        self._threads: list[threading.Thread] = []
        self._counter = 0
        self._lock = threading.Lock()
        self._closed = False
        for index, work_queue in enumerate(self._queues):
            thread = threading.Thread(
                target=self._worker,
                args=(work_queue,),
                name=f"omnicrawler-browser-{index}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def _fetch_timeout(self) -> float:
        return float(self.config.section("http").get("timeout_seconds", 25)) + 30

    def fetch(self, request: CrawlRequest) -> FetchResult:
        task = _PoolTask(request, threading.Event())
        with self._lock:
            if self._closed:
                raise RuntimeError("Browser pool is closed")
            work_queue = self._queues[self._counter % len(self._queues)]
            self._counter += 1
            work_queue.put(task)  # 锁内入队，防止 close() 插入 None 哨兵
        timeout = self._fetch_timeout()
        if not task.done.wait(timeout):
            # S2.5.11：调用方超时——标记丢弃（worker 不再渲染/渲染后释放资源）
            task.discarded.set()
            raise TimeoutError(f"Browser pool worker did not finish within {timeout:g} seconds")
        if task.error is not None:
            raise task.error
        if task.result is None:
            raise RuntimeError("Browser pool returned no result")
        return task.result

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        for work_queue in self._queues:
            work_queue.put(None)
        for thread in self._threads:
            thread.join(timeout=10)

    def _worker(self, work_queue: queue.Queue) -> None:
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as playwright:
                browser_config = self.config.section("browser")
                # B03-006：Playwright 路径同样拒绝 launch_args 关闭 TLS 校验，
                # 并显式尊重 http.verify_tls（默认开启）。
                launch_args = []
                for item in browser_config.get("launch_args", []):
                    arg = str(item)
                    if arg == "--ignore-certificate-errors" or "--ignore-certificate-errors=" in arg:
                        raise ValueError(
                            "browser.launch_args 禁止关闭 TLS 校验（--ignore-certificate-errors）；"
                            "如需关闭请用可审计的 http.verify_tls=false"
                        )
                    launch_args.append(arg)
                verify_tls = bool(self.config.section("http").get("verify_tls", True))
                if not verify_tls:
                    launch_args.append("--ignore-certificate-errors")
                    LOGGER.warning("Playwright 路径 verify_tls=false：TLS 校验已关闭（仅限受控内网站点）")
                browser = playwright.chromium.launch(
                    headless=bool(browser_config.get("headless", True)),
                    args=launch_args,
                )
                contexts: dict[str, Any] = {}
                try:
                    while True:
                        task = work_queue.get()
                        if task is None:
                            return
                        self._handle_task(browser, contexts, task)
                finally:
                    for context in contexts.values():
                        try:
                            context.close()
                        except Exception as exc:
                            LOGGER.debug("Browser cleanup error: %s", exc)
                    browser.close()
        except Exception as startup_error:
            while True:
                task = work_queue.get()
                if task is None:
                    return
                task.error = startup_error
                task.done.set()

    def _handle_task(
        self, browser: Any, contexts: dict[str, Any], task: _PoolTask,
    ) -> None:
        """S2.5.11：单个任务的统一处理——丢弃检查、渲染、超时后资源释放。"""
        if task.discarded.is_set():
            task.error = TimeoutError("任务已被丢弃（调用方等待超时）")
            task.done.set()
            return
        try:
            task.result = self._render(browser, contexts, task.request)
            if task.discarded.is_set():
                # 渲染期间被标记丢弃：关闭该 context 并移除，防资源滞留
                context_key = self._context_key(task.request)
                context = contexts.pop(context_key, None)
                if context is not None:
                    try:
                        context.close()
                    except Exception as exc:
                        LOGGER.debug("Browser cleanup error: %s", exc)
        except BaseException as exc:
            task.error = exc
        finally:
            task.done.set()

    def _render(self, browser: Any, contexts: dict[str, Any], request: CrawlRequest) -> FetchResult:
        context_key = self._context_key(request)
        for attempt in range(2):
            context = contexts.get(context_key)
            if context is None:
                context = contexts[context_key] = self._new_context(browser, context_key, request)
            page = context.new_page()
            api_candidates: list[dict[str, Any]] = []
            try:
                page.route(
                    "**/*", lambda route, req=request: self._guard_route(route, target_url=req.url)
                )
                page.on("response", lambda response, ac=api_candidates: self._capture_response(response, ac))
                started = time.monotonic()
                browser_config = self.config.section("browser")
                response = page.goto(
                    request.url,
                    wait_until=str(browser_config.get("wait_until", "networkidle")),
                    timeout=int(float(self.config.section("http").get("timeout_seconds", 25)) * 1000),
                )
                run_actions_for_page(page, browser_config.get("actions", []))
                body = page.content().encode("utf-8")
                final_url = page.url
                self.egress.authorize(final_url, purpose="browser", count_request=False)
                maximum = int(self.config.section("http").get("max_response_bytes", 50_000_000))
                if len(body) > maximum:
                    raise ResponseTooLargeError(f"浏览器页面超过大小限制: {len(body)} > {maximum}")
                self.egress.record_response(len(body), url=final_url)
                self._save_context(context, context_key)
                headers = {
                    "content-type": "text/html; charset=utf-8",
                    "x-omnicrawler-api-candidates": json.dumps(
                        [
                            {key: value for key, value in item.items() if key not in {"json", "text"}}
                            for item in api_candidates[:100]
                        ],
                        ensure_ascii=False,
                    ),
                }
                return FetchResult(
                    request,
                    final_url,
                    response.status if response else 200,
                    headers,
                    body,
                    time.monotonic() - started,
                    {"api_responses": api_candidates},
                )
            except Exception as exc:
                if attempt == 0:
                    LOGGER.warning("浏览器渲染第 1 次尝试失败: %s", exc)
                    try:
                        context.close()
                    except Exception as exc:
                        LOGGER.debug("Browser cleanup error: %s", exc)
                    contexts.pop(context_key, None)
                    continue
                raise
            finally:
                try:
                    page.close()
                except Exception as exc:
                    LOGGER.debug("Browser cleanup error: %s", exc)
        raise RuntimeError("Unreachable browser retry state")

    def _context_key(self, request: CrawlRequest) -> str:
        session = self.config.section("session")
        account = str(request.meta.get("account") or session.get("name", "default"))
        proxy = str(request.meta.get("proxy") or self.config.section("http").get("proxy", ""))
        return f"{account}|{proxy}"

    def _state_path(self, context_key: str) -> Path | None:
        if not self.config.section("session").get("persist_cookies", False):
            return None
        safe_name = "".join(char if char.isalnum() or char in "-_" else "_" for char in context_key)[:120]
        return self.config.workspace / "sessions" / f"{safe_name}.playwright.json"

    def _new_context(self, browser: Any, context_key: str, request: CrawlRequest) -> Any:
        state_path = self._state_path(context_key)
        options: dict[str, Any] = {"user_agent": self.config.section("http").get("user_agent")}
        if state_path and state_path.is_file():
            options["storage_state"] = str(state_path)
        # S2.5.13：与 _context_key 同源——meta 代理优先，否则配置代理
        # FINAL-S9：代理统一过 NetworkTargetPolicy——与 http/async 引擎既有
        # 行为一致（二者自始即校验配置代理），浏览器路径此前是唯一未校验的旁路。
        # 注意：本地网关代理（如 127.0.0.1:7890）属私网目标，默认被拒——
        # 需要时在 http 段显式开启 allow_private_network: true（三引擎通用开关）。
        proxy = str(request.meta.get("proxy") or self.config.section("http").get("proxy", ""))
        if proxy:
            self.target_policy.require(proxy)
            options["proxy"] = {"server": proxy}
        context = browser.new_context(**options)
        # -- 反检测增强：注入 stealth.min.js + 隐藏 webdriver 标记 --
        # FINAL-U9 口径对齐（与 stealth_enhanced.py "实验性、仅显式启用"的注释不同）：
        # 默认 Playwright 路径在 stealth.min.js 存在时即无条件注入并隐藏
        # navigator.webdriver——这是**默认行为**而非仅实验分支；README 尾部
        # "不绕过站点安全策略"的合规边界请以此实际行为为准评估。如需关闭，
        # 移除安装目录中的 stealth.min.js 即可停用注入。
        stealth_path = Path(__file__).resolve().parent / "stealth.min.js"
        if stealth_path.is_file():
            try:
                context.add_init_script(path=str(stealth_path))
            except Exception as exc:
                LOGGER.warning("Stealth script injection failed: %s", exc)
        try:
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
        except Exception as exc:
            LOGGER.warning("Stealth script injection failed: %s", exc)
        extra_headers = {
            **self.config.section("http").get("headers", {}),
            **self.config.section("source").get("headers", {}),
            **request.headers,
        }
        if extra_headers:
            context.set_extra_http_headers({str(key): str(value) for key, value in extra_headers.items()})
        return context

    def _save_context(self, context: Any, context_key: str) -> None:
        state_path = self._state_path(context_key)
        if state_path is None:
            return
        state_path.parent.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(state_path))
        try:
            os.chmod(state_path, 0o600)
        except OSError:
            pass

    def _guard_route(self, route: Any, *, target_url: str = "") -> None:
        try:
            headers = getattr(route.request, "headers", {}) or {}
            headers = headers if isinstance(headers, dict) else {}
            egress = getattr(self, "egress", None)
            if egress is None:
                self.target_policy.require(route.request.url)
            else:
                egress.authorize(
                    route.request.url,
                    purpose="browser",
                    headers=headers,
                )
            # S1.3.4：跨来源（CDN/三方脚本/分析）请求剥除认证凭据头。
            stripped = strip_cross_origin_credentials(headers, target_url, route.request.url)
            if stripped is not None:
                route.continue_(headers=stripped)
                return
        except PermissionError:
            route.abort("blockedbyclient")
        else:
            route.continue_()

    def _capture_response(self, response: Any, output: list[dict[str, Any]]) -> None:
        try:
            resource_type = response.request.resource_type
            content_type = response.headers.get("content-type", "")
            if resource_type in {"xhr", "fetch"} or "json" in content_type:
                entry: dict[str, Any] = {
                    "url": response.url,
                    "method": response.request.method,
                    "status": response.status,
                    "resource_type": resource_type,
                    "content_type": content_type,
                }
                request_headers = getattr(response.request, "headers", {}) or {}
                if isinstance(request_headers, dict):
                    safe_names = {"accept", "content-type", "x-requested-with", "origin", "referer"}
                    entry["request_headers"] = {
                        str(key): str(value) for key, value in request_headers.items()
                        if str(key).casefold() in safe_names
                    }
                if response.request.method.upper() not in {"GET", "HEAD"}:
                    try:
                        post_data = response.request.post_data
                        if post_data:
                            try:
                                entry["request_payload"] = json.loads(post_data)
                            except (TypeError, json.JSONDecodeError):
                                entry["request_payload"] = post_data
                    except Exception as exc:
                        LOGGER.info("Evidence capture failed, skipping: %s", exc)
                browser = self.config.section("browser")
                if browser.get("capture_api_responses", True):
                    per_response = max(0, int(browser.get("max_api_response_bytes", 1_000_000)))
                    total_limit = max(0, int(browser.get("max_api_capture_bytes", 10_000_000)))
                    captured = sum(int(item.get("captured_bytes", 0)) for item in output)
                    declared = response.headers.get("content-length", "")
                    # S1.3.6：先在 content-length 上拒绝超大响应，并先计入预算，
                    # 避免把超大响应整体读进内存。
                    declared_len = int(declared) if declared.isdigit() else -1
                    if (
                        (declared_len > per_response and declared_len != -1)
                        or captured + (declared_len if declared_len != -1 else 0) > total_limit
                    ):
                        entry["capture_skipped"] = "size_limit"
                    else:
                        body = response.body()
                        if len(body) <= per_response and captured + len(body) <= total_limit:
                            egress = getattr(self, "egress", None)
                            if egress is not None:
                                egress.record_response(len(body), url=response.url)
                            entry["captured_bytes"] = len(body)
                            text = body.decode("utf-8", errors="replace")
                            if "json" in content_type:
                                parsed = safe_json_loads(text)
                                if parsed is not None:
                                    entry["json"] = parsed
                                else:
                                    entry["text"] = text
                            else:
                                entry["text"] = text
                        else:
                            entry["capture_skipped"] = "size_limit"
                output.append(entry)
        except EgressBudgetExceededError:
            egress = getattr(self, "egress", None)
            if egress is not None:
                egress.disconnect_task()
        except Exception as exc:
            LOGGER.info("Evidence capture failed, skipping: %s", exc)
