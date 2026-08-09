"""Crawl4AI 桥接 — 将 crawl4ai 的 AI 驱动抓取能力集成到 OmniCrawler。

功能:
    - 轻量 JS 渲染（比 Playwright 省 5-10x 资源）
    - LLM 友好的 Markdown 输出
    - 自适应爬取（学习网站模式）
    - Undetected 浏览器模式（绕过 Cloudflare/Akamai）
    - 虚拟滚动支持（无限滚动页面）
    - CSS/XPath/LLM 多策略结构化提取
    - 内存自适应批处理调度

依赖: omnicrawl[crawl4ai]  或  pip install crawl4ai
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..security.policy import NetworkTargetPolicy

logger = logging.getLogger(__name__)


# ── 结果模型 ──────────────────────────────────────────────────────────

@dataclass
class C4AResult:
    """crawl4ai 抓取结果，兼容 OmniCrawler FetchResult。"""
    url: str
    final_url: str = ""
    status: int = 200
    markdown: str = ""
    html: str = ""
    text: str = ""
    title: str = ""
    extracted: dict[str, Any] = field(default_factory=dict)
    links: list[str] = field(default_factory=list)
    media: list[str] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    screenshot: bytes | None = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url, "final_url": self.final_url, "status": self.status,
            "markdown": self.markdown[:5000], "title": self.title,
            "extracted": self.extracted, "links_count": len(self.links),
            "media_count": len(self.media), "tables_count": len(self.tables),
            "error": self.error,
        }


# ── 配置模型 ──────────────────────────────────────────────────────────

@dataclass
class C4AConfig:
    """crawl4ai 抓取配置。"""
    # 浏览器
    headless: bool = True
    browser_type: str = "chromium"       # chromium / undetected
    viewport_width: int = 1280
    viewport_height: int = 800
    user_agent: str = ""

    # 抓取
    wait_until: str = "networkidle"      # commit / domcontentloaded / networkidle
    timeout_ms: int = 30000
    word_count_threshold: int = 10       # 低于此字数视为无内容
    cache_mode: str = "bypass"           # enabled / bypass / disabled / write_only

    # 提取
    extraction_strategy: str = ""        # css / xpath / llm / cosine / ""
    extraction_schema: dict[str, Any] | None = None
    css_selector: str = ""               # 若指定，仅提取此选择器内的内容
    excluded_selector: str = ""

    # Markdown
    markdown_generator: str = "default"

    # 代理
    proxy: str = ""                      # http://user:pass@host:port
    proxy_rotation: str = ""             # round_robin / random
    allow_private_network: bool = False

    # LLM（用于 extraction_strategy="llm"）
    llm_provider: str = ""               # openai / anthropic / ollama
    llm_api_key: str = ""
    llm_model: str = ""

    # 自适应
    adaptive: bool = False
    adaptive_max_pages: int = 20
    adaptive_max_depth: int = 5

    # 虚拟滚动
    virtual_scroll: bool = False
    scroll_count: int = 10
    scroll_selector: str = ""

    # 链接发现
    score_links: bool = False
    link_score_threshold: float = 0.3

    def __post_init__(self) -> None:
        if self.browser_type not in {"chromium", "undetected"}:
            raise ValueError("browser_type只能是chromium或undetected")
        if self.wait_until not in {"commit", "domcontentloaded", "load", "networkidle"}:
            raise ValueError("wait_until配置无效")
        if not 1 <= int(self.timeout_ms) <= 600_000:
            raise ValueError("timeout_ms必须在1到600000之间")
        if self.viewport_width < 320 or self.viewport_height < 240:
            raise ValueError("浏览器视口尺寸过小")
        if self.adaptive_max_pages < 1 or self.adaptive_max_depth < 0:
            raise ValueError("自适应抓取范围无效")
        if self.scroll_count < 1:
            raise ValueError("scroll_count必须至少为1")

    def to_browser_config(self) -> Any:
        try:
            from crawl4ai import BrowserConfig
        except ImportError:
            raise RuntimeError("crawl4ai 未安装，请执行 pip install crawl4ai")
        kwargs: dict[str, Any] = {
            "headless": self.headless,
            "browser_type": self.browser_type,
        }
        if self.viewport_width and self.viewport_height:
            kwargs["viewport_width"] = self.viewport_width
            kwargs["viewport_height"] = self.viewport_height
        if self.user_agent:
            kwargs["user_agent"] = self.user_agent
        if self.proxy:
            kwargs["proxy_config"] = {"server": self.proxy}
        return BrowserConfig(**kwargs)

    def to_crawler_config(self) -> Any:
        try:
            from crawl4ai import CacheMode, CrawlerRunConfig
        except ImportError:
            raise RuntimeError("crawl4ai 未安装，请执行 pip install crawl4ai")
        kwargs: dict[str, Any] = {
            "wait_until": self.wait_until,
            "page_timeout": self.timeout_ms,
            "word_count_threshold": self.word_count_threshold,
        }
        cache_map = {
            "enabled": CacheMode.ENABLED, "bypass": CacheMode.BYPASS,
            "disabled": CacheMode.DISABLED, "write_only": CacheMode.WRITE_ONLY,
        }
        kwargs["cache_mode"] = cache_map.get(self.cache_mode, CacheMode.BYPASS)
        if self.css_selector:
            kwargs["css_selector"] = self.css_selector
        if self.excluded_selector:
            kwargs["excluded_selector"] = self.excluded_selector

        # 提取策略
        if self.extraction_strategy and self.extraction_schema:
            kwargs["extraction_strategy"] = self._build_extraction_strategy()

        # 虚拟滚动
        if self.virtual_scroll:
            from crawl4ai import VirtualScrollConfig
            kwargs["virtual_scroll_config"] = VirtualScrollConfig(
                container_selector=self.scroll_selector or "",
                scroll_count=self.scroll_count,
            )

        # 链接评分
        if self.score_links:
            from crawl4ai import LinkPreviewConfig
            kwargs["link_preview_config"] = LinkPreviewConfig(
                score_threshold=self.link_score_threshold,
            )
            kwargs["score_links"] = True

        return CrawlerRunConfig(**kwargs)

    def _build_extraction_strategy(self) -> Any:
        from crawl4ai import (
            JsonCssExtractionStrategy,
            JsonXPathExtractionStrategy,
            LLMExtractionStrategy,
        )
        schema = self.extraction_schema or {}
        if self.extraction_strategy == "css":
            return JsonCssExtractionStrategy(schema)
        elif self.extraction_strategy == "xpath":
            return JsonXPathExtractionStrategy(schema)
        elif self.extraction_strategy == "llm":
            return LLMExtractionStrategy(
                provider=self.llm_provider or "openai",
                api_token=self.llm_api_key,
                schema=schema,
                instruction="Extract the structured data from the page.",
            )
        else:
            return JsonCssExtractionStrategy(schema)


# ── 核心引擎 ──────────────────────────────────────────────────────────

class Crawl4AIEngine:
    """crawl4ai 抓取引擎 — 轻量 JS 渲染 + AI 提取。

    支持注入 EgressBroker：注入后所有抓取走 审计/预算/熔断 边界
    （S2.5.5），不再走直连校验；未注入时保留轻量 NetworkTargetPolicy 直连校验。
    """

    def __init__(
        self,
        config: C4AConfig | None = None,
        *,
        egress: Any | None = None,
    ) -> None:
        self.config = config or C4AConfig()
        self.egress = egress
        self._available: bool | None = None

    @property
    def available(self) -> bool:
        # S4.5 P3#154：仅成功缓存；import 失败不缓存（运行时安装后下次探测可发现）
        if self._available is True:
            return True
        try:
            import crawl4ai  # noqa: F401
            self._available = True
            return True
        except ImportError:
            return False

    def fetch(self, url: str, *, config: C4AConfig | None = None) -> C4AResult:
        """同步抓取单个 URL。"""
        cfg = config or self.config
        self._authorize(url, cfg)
        if not self.available:
            raise RuntimeError("crawl4ai 未安装，请执行 pip install crawl4ai 或 pip install omnicrawl[crawl4ai]")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            try:
                result = asyncio.run(self._fetch_async(url, cfg))
            except Exception as exc:
                logger.exception("crawl4ai 抓取失败")
                result = C4AResult(url=url, status=0, error=f"{type(exc).__name__}: {exc}")
        else:
            result_box: list[C4AResult] = []
            error_box: list[Exception] = []

            def _run():
                try:
                    result_box.append(asyncio.run(self._fetch_async(url, cfg)))
                except Exception as exc:
                    error_box.append(exc)

            thread = threading.Thread(target=_run, name="c4a-fetch", daemon=True)
            thread.start()
            thread.join(timeout=cfg.timeout_ms / 1000 + 30)
            if result_box:
                result = result_box[0]
            elif error_box:
                err = error_box[0]
                logger.error("crawl4ai 抓取失败: %s: %s", type(err).__name__, err)
                result = C4AResult(url=url, status=0, error=f"{type(err).__name__}: {err}")
            else:
                result = C4AResult(url=url, status=0, error="crawl4ai 抓取超时")
        self._record_result(url, result)
        return result

    async def fetch_async(self, url: str, *, config: C4AConfig | None = None) -> C4AResult:
        cfg = config or self.config
        self._authorize(url, cfg)
        if not self.available:
            raise RuntimeError("crawl4ai 未安装")
        result = await self._fetch_async(url, cfg)
        self._record_result(url, result)
        return result

    async def fetch_many(
        self, urls: list[str], *, config: C4AConfig | None = None,
    ) -> list[C4AResult]:
        cfg = config or self.config
        for url in urls:
            self._authorize(url, cfg)
        if not self.available:
            raise RuntimeError("crawl4ai 未安装")
        try:
            from crawl4ai import AsyncWebCrawler
            browser_cfg = cfg.to_browser_config()
            crawler_cfg = cfg.to_crawler_config()
            results: list[C4AResult] = []
            async with AsyncWebCrawler(config=browser_cfg) as crawler:
                for url in urls:
                    raw = await crawler.arun(url, config=crawler_cfg)
                    results.append(self._convert(raw))
            for url, result in zip(urls, results, strict=False):
                self._record_result(url, result)
            return results
        except Exception as exc:
            logger.exception("crawl4ai 批量抓取失败")
            for url in urls:
                self._record_result(url, C4AResult(url=url, status=0, error=str(exc)))
            return [C4AResult(url=u, status=0, error=str(exc)) for u in urls]

    async def adaptive_fetch(
        self, start_url: str, query: str = "", *, config: C4AConfig | None = None,
    ) -> list[C4AResult]:
        """自适应抓取：自动学习网站模式，深度探索。"""
        cfg = config or self.config
        self._authorize(start_url, cfg)
        if not self.available:
            raise RuntimeError("crawl4ai 未安装")
        try:
            from crawl4ai import AdaptiveConfig, AdaptiveCrawler, AsyncWebCrawler
            browser_cfg = cfg.to_browser_config()
            adaptive_cfg = AdaptiveConfig(
                max_depth=cfg.adaptive_max_depth,
                max_pages=cfg.adaptive_max_pages,
            )
            async with AsyncWebCrawler(config=browser_cfg) as crawler:
                adaptive = AdaptiveCrawler(crawler, adaptive_cfg)
                state = await adaptive.digest(start_url=start_url, query=query or "")
                results = [self._convert(r) for r in state.results] if hasattr(state, "results") else []
            for item in results:
                self._record_result(item.url, item)
            return results
        except Exception as exc:
            logger.exception("crawl4ai 自适应抓取失败")
            return [C4AResult(url=start_url, status=0, error=str(exc))]

    def deep_crawl(
        self, start_url: str, *, config: C4AConfig | None = None,
        max_pages: int = 100, max_depth: int = 3,
    ) -> list[C4AResult]:
        """BFS 深度爬取整个网站。"""
        cfg = config or self.config
        self._authorize(start_url, cfg)
        if not self.available:
            raise RuntimeError("crawl4ai 未安装")
        result_box: list[list[C4AResult]] = []

        def _run():
            async def _async():
                try:
                    from crawl4ai import (
                        AsyncWebCrawler,
                        BFSDeepCrawlStrategy,
                        DomainFilter,
                        FilterChain,
                    )
                    browser_cfg = cfg.to_browser_config()
                    filter_chain = FilterChain([
                        DomainFilter(allowed_domains=[self._extract_domain(start_url)]),
                    ])
                    strategy = BFSDeepCrawlStrategy(
                        max_depth=max_depth, max_pages=max_pages,
                        filter_chain=filter_chain,
                    )
                    async with AsyncWebCrawler(config=browser_cfg) as crawler:
                        raw_results = await crawler.arun(url=start_url, config=strategy)
                        converted = [self._convert(r) for r in raw_results] if raw_results else []
                        for item in converted:
                            self._record_result(item.url, item)
                        result_box.append(converted)
                except Exception as exc:
                    logger.exception("crawl4ai 深度爬取失败")
                    result_box.append([C4AResult(url=start_url, status=0, error=str(exc))])
            asyncio.run(_async())

        thread = threading.Thread(target=_run, name="c4a-deep", daemon=True)
        thread.start()
        thread.join(timeout=600)
        return result_box[0] if result_box else [C4AResult(url=start_url, status=0, error="crawl4ai 深度抓取超时")]

    # ── 内部 ──────────────────────────────────────────────────────────

    def _authorize(self, url: str, cfg: C4AConfig) -> None:
        """S2.5.5：egress 注入时走 broker 审计/预算/熔断边界；否则直连校验。"""
        if self.egress is not None:
            self.egress.authorize(url, purpose="browser")
            return
        _require_target(url, cfg)

    def _record_result(self, url: str, result: C4AResult) -> None:
        """S2.5.5：把抓取结果计入 egress 审计（响应字节/成功/失败）。"""
        if self.egress is None:
            return
        size = len(result.html) + len(result.markdown) + len(result.text)
        self.egress.record_response(size, url=url)
        if result.status and not result.error:
            self.egress.record_success(url)
        else:
            self.egress.record_failure(
                url, error=result.error or f"status={result.status}"
            )

    async def _fetch_async(self, url: str, cfg: C4AConfig) -> C4AResult:
        from crawl4ai import AsyncWebCrawler
        browser_cfg = cfg.to_browser_config()
        crawler_cfg = cfg.to_crawler_config()
        async with AsyncWebCrawler(config=browser_cfg) as crawler:
            raw = await crawler.arun(url, config=crawler_cfg)
            return self._convert(raw)

    def _convert(self, raw: Any) -> C4AResult:
        """将 crawl4ai CrawlResult 转换为 C4AResult。"""
        try:
            # S2.5.5：metadata 可能为 None，统一兜底空 dict 防 .get 崩溃
            metadata = getattr(raw, "metadata", None)
            metadata = metadata if isinstance(metadata, dict) else {}
            # S2.5.5：status_code 真实透传（404/403 不再兜底成 200），仅 0/None 回退 200
            status = getattr(raw, "status_code", None)
            status = status if isinstance(status, int) and status > 0 else 200
            return C4AResult(
                url=getattr(raw, "url", ""),
                final_url=getattr(raw, "url", ""),
                status=status,
                markdown=getattr(raw, "markdown", "") or "",
                html=getattr(raw, "html", "") or "",
                text=getattr(raw, "text", "") or "",
                title=metadata.get("title", ""),
                extracted=getattr(raw, "extracted_content", {}) or {},
                links=list(getattr(raw, "links", []) or []),
                media=list(getattr(raw, "media", []) or []),
                tables=list(getattr(raw, "tables", []) or []),
                metadata=metadata,
                screenshot=getattr(raw, "screenshot", None),
            )
        except Exception as exc:
            return C4AResult(url=str(raw), status=0, error=str(exc))

    @staticmethod
    def _extract_domain(url: str) -> str:
        from urllib.parse import urlparse
        return urlparse(url).netloc


# ── 便捷函数 ──────────────────────────────────────────────────────────

def fetch_js_page(url: str) -> C4AResult:
    """快速抓取一个 JS 重度页面，返回 Markdown。"""
    return Crawl4AIEngine().fetch(url)


def fetch_structured(url: str, schema: dict[str, Any]) -> C4AResult:
    """用 CSS/XPath schema 提取结构化数据。"""
    config = C4AConfig(extraction_strategy="css", extraction_schema=schema)
    return Crawl4AIEngine(config).fetch(url)


def fetch_stealth(url: str) -> C4AResult:
    """用 undetected 模式绕过反爬。"""
    config = C4AConfig(browser_type="undetected")
    return Crawl4AIEngine(config).fetch(url)


class _C4ANetworkConfig:
    def __init__(self, allow_private: bool):
        self.allow_private = allow_private

    def section(self, name: str) -> dict[str, Any]:
        if name != "http":
            return {}
        return {
            "allow_private_network": self.allow_private,
            "resolve_dns": True,
            "dns_fail_closed": True,
            "dns_cache_ttl_seconds": 60,
        }


def _require_target(url: str, config: C4AConfig) -> None:
    parts = urlsplit(url)
    if parts.username is not None or parts.password is not None:
        raise ValueError("目标 URL 不允许包含明文凭据")
    NetworkTargetPolicy(_C4ANetworkConfig(config.allow_private_network)).require(url)  # type: ignore[arg-type]


# ── CLI ────────────────────────────────────────────────────────────────
def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Crawl4AI 桥接 — 轻量 JS 渲染抓取")
    parser.add_argument("url", help="目标 URL")
    parser.add_argument("--stealth", action="store_true", help="使用 undetected 模式")
    parser.add_argument("--extract", help="CSS/XPath 提取 schema JSON 文件")
    parser.add_argument("-o", "--output", help="输出 JSON 文件路径")
    args = parser.parse_args()

    config = C4AConfig(browser_type="undetected" if args.stealth else "chromium")
    if args.extract:
        with open(args.extract, encoding="utf-8") as fh:
            schema = json.load(fh)
        config.extraction_strategy = "css"
        config.extraction_schema = schema

    engine = Crawl4AIEngine(config)
    result = engine.fetch(args.url)
    output = json.dumps(result.to_dict(), ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(f"已写入: {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
