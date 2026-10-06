from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.config import DEFAULTS, AppConfig
from ..core.models import CrawlRequest, FetchResult
from ..core.utils import user_agent
from ..security.policy import RobotsPolicy
from .http_client import HTTPFetcher
from .routing import needs_browser


def build_inspection_config(
    url: str,
    *,
    timeout_seconds: float = 20.0,
    robots_fail_closed: bool = True,
    allow_private_network: bool = False,
) -> AppConfig:
    """构造"巡检 / 分析页面用"的 AppConfig：守卫与 crawl 完全一致。

    `inspect_url`（站点识别）与 `fetch_page_html`（分析页面并填字段）都走这里 ——
    否则会出现**第二条不带 SSRF/重定向/大小/robots 守卫的抓取路径**，
    那正是本项目反复避免的东西。
    """
    raw = copy.deepcopy(DEFAULTS)
    raw["project"] = {"name": "site_inspection", "workspace": "work/site_inspection"}
    raw["source"] = {"kind": "static_html", "seeds": [url]}
    raw["http"].update({
        "user_agent": user_agent("Inspector (+contact: local-user)"),
        "timeout_seconds": timeout_seconds,
        "retries": 1,
        "max_response_bytes": 10_000_000,
        "respect_robots": True,
        "robots_fail_closed": bool(robots_fail_closed),
        # 沿用调用方（任务）的出网策略：默认仍禁止本机/内网/保留地址，
        # 用户在配置里显式放行时才跟着放行 —— 分析不该比运行更宽松，也不该更严格。
        "allow_private_network": bool(allow_private_network),
    })
    root = Path.cwd().resolve()
    return AppConfig(
        root / ".omnicrawler-inspector.yaml", root, raw, root / "work" / "site_inspection"
    )


def _guarded_fetch(url: str, config: AppConfig, fetcher: Any | None = None) -> FetchResult:
    """按 robots 策略放行后抓一页。

    传入 `fetcher` 时要求**它自身经 EgressBroker 审计**（例如 AsyncFetcher）；
    否则回退为独立的 HTTPFetcher 实例。
    """
    if not RobotsPolicy(config).allowed(url):
        raise PermissionError("robots.txt does not allow automated inspection of this URL")
    request = CrawlRequest(url, meta={"root_url": url})
    if fetcher is not None:
        return fetcher.fetch(request)
    return HTTPFetcher(config).fetch(request)


def fetch_analysis_page(
    url: str, *, timeout_seconds: float = 20.0, robots_fail_closed: bool = True,
    allow_private_network: bool = False, fetcher: Any | None = None,
    force_browser: bool = False,
    browser_reason: Callable[[FetchResult], str] | None = None,
) -> tuple[FetchResult, bool, str]:
    """Inspect one page and, when justified, render it once through guarded product I/O."""
    config = build_inspection_config(
        url, timeout_seconds=timeout_seconds, robots_fail_closed=robots_fail_closed,
        allow_private_network=allow_private_network,
    )
    result = _guarded_fetch(url, config, fetcher=fetcher)
    reason = browser_reason(result) if browser_reason is not None else needs_browser(result)[1]
    if not reason and not force_browser:
        return result, False, ""
    from ..fetching.browser_fetcher import BrowserFetcher

    # The browser has the same network/robots policy, a bounded timeout and no actions.
    config.section("browser").update({"headless": True, "pool_size": 1})
    try:
        if not RobotsPolicy(config).allowed(result.final_url or url):
            raise PermissionError("robots.txt does not allow rendered inspection of this URL")
        with BrowserFetcher(config) as browser:
            rendered = browser.fetch(CrawlRequest(result.final_url or url, render=True,
                                                 meta={"root_url": url}))
        if rendered.status >= 400 or not rendered.body.strip():
            raise RuntimeError(f"浏览器补充失败：HTTP {rendered.status} 或内容为空")
        return rendered, True, ""
    except PermissionError:
        raise
    except Exception as exc:  # noqa: BLE001 - retain the static evidence and explicit failure
        return result, False, f"浏览器补充未完成：{type(exc).__name__}: {exc}"



def fetch_static_page(
    url: str, *, config: AppConfig | None = None, fetcher: Any | None = None, egress: Any | None = None,
) -> FetchResult:
    """Bounded static inspection with the task's broker and robots policy."""
    config = config or getattr(fetcher, "config", None) or getattr(egress, "config", None) or build_inspection_config(url)
    client = fetcher or HTTPFetcher(config, egress=egress, purpose="change_monitor")
    broker = getattr(client, "egress", None)
    if broker is None:
        raise RuntimeError("页面探测必须使用统一出口 Broker")
    if not RobotsPolicy(config, egress=broker).allowed(url):
        raise PermissionError("robots.txt does not allow automated inspection of this URL")
    result = client.fetch(CrawlRequest(url, meta={"root_url": url}))
    if result.status >= 400:
        raise RuntimeError(f"页面探测失败：HTTP {result.status}")
    return result
