"""Crawl4AI offline Markdown/CSS/XPath processing over guarded native rendering."""

from __future__ import annotations

import asyncio
import copy
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..core.config import DEFAULTS, AppConfig
from ..core.models import CrawlRequest
from ..fetching.browser_fetcher import BrowserFetcher
from ..security.egress import EgressBroker
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
    wait_until: str = "domcontentloaded"      # commit / domcontentloaded / networkidle
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
    """Native guarded rendering with offline Crawl4AI processing."""

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
        cfg = config or self.config
        _require_target(url, cfg) if self.egress is None else None
        self._validate_modes(cfg)
        if not self.available:
            raise RuntimeError("crawl4ai 未安装")
        try:
            return self._fetch_guarded(url, cfg)
        except Exception as exc:
            logger.exception("crawl4ai guarded fetch failed")
            return C4AResult(url=url, status=0, error=f"{type(exc).__name__}: {exc}")

    async def fetch_async(self, url: str, *, config: C4AConfig | None = None) -> C4AResult:
        # Shield the owned render so cancellation cannot orphan a background crawler.
        job = asyncio.create_task(asyncio.to_thread(self.fetch, url, config=config))
        try:
            return await asyncio.shield(job)
        except asyncio.CancelledError:
            if self.egress is not None:
                self.egress.disconnect_task()
            await asyncio.shield(job)
            raise

    async def fetch_many(self, urls: list[str], *, config: C4AConfig | None = None) -> list[C4AResult]:
        if len(urls) > 1000:
            raise ValueError("Crawl4AI batch exceeds 1000 URLs")
        return [await self.fetch_async(url, config=config) for url in urls]

    async def adaptive_fetch(self, start_url: str, query: str = "", *, config: C4AConfig | None = None) -> list[C4AResult]:
        raise ValueError("Crawl4AI autonomous networking is unavailable; use the native focused scheduler")

    def deep_crawl(self, start_url: str, *, config: C4AConfig | None = None,
                   max_pages: int = 100, max_depth: int = 3) -> list[C4AResult]:
        from collections import deque
        from urllib.parse import urldefrag, urljoin
        if type(max_pages) is not int or not 1 <= max_pages <= 1000 or type(max_depth) is not int or not 0 <= max_depth <= 20:
            raise ValueError("Crawl4AI crawl bounds are invalid")
        queue = deque([(start_url, 0)])
        seen = {start_url}
        results: list[C4AResult] = []
        while queue and len(results) < max_pages:
            url, depth = queue.popleft()
            result = self.fetch(url, config=config)
            results.append(result)
            if result.error or not 200 <= result.status < 300 or depth >= max_depth:
                continue
            for link in result.links:
                target = urldefrag(urljoin(result.final_url, link))[0]
                if target not in seen and urlsplit(target).hostname == urlsplit(start_url).hostname:
                    if len(seen) >= max_pages:
                        break
                    seen.add(target)
                    queue.append((target, depth + 1))
        return results

    @staticmethod
    def _validate_modes(cfg: C4AConfig) -> None:
        if cfg.browser_type != "chromium" or cfg.adaptive or cfg.virtual_scroll or cfg.score_links:
            raise ValueError("Use native browser readiness/collection/focused scheduling for this mode")
        if cfg.extraction_strategy not in {"", "css", "xpath"}:
            raise ValueError("Crawl4AI bridge supports offline CSS/XPath; use native AI for model extraction")
        if cfg.proxy_rotation or cfg.markdown_generator != "default" or cfg.cache_mode != "bypass":
            raise ValueError("Crawl4AI bridge requires native networking and bypass cache")

    def _native_config(self, url: str, cfg: C4AConfig) -> AppConfig:
        base = self.egress.config if self.egress is not None else None
        raw = copy.deepcopy(base.raw if base is not None else DEFAULTS)
        if base is None:
            raw["source"]["seeds"] = [url]
            raw["http"]["allow_private_network"] = cfg.allow_private_network
        raw["browser"].update(engine="playwright", headless=cfg.headless, pool_size=1,
                              wait_until=cfg.wait_until, viewport={"width": cfg.viewport_width, "height": cfg.viewport_height})
        raw["http"]["timeout_seconds"] = min(float(raw["http"]["timeout_seconds"]), cfg.timeout_ms / 1000)
        if cfg.user_agent:
            raw["http"]["user_agent"] = cfg.user_agent
        if cfg.proxy:
            raw["http"]["proxy"] = cfg.proxy
        root = base.root if base is not None else Path.cwd()
        return AppConfig(base.path if base is not None else root / "crawl4ai.yaml", root, raw,
                         base.workspace if base is not None else root / "work" / "crawl4ai")

    def _fetch_guarded(self, url: str, cfg: C4AConfig) -> C4AResult:
        native = self._native_config(url, cfg)
        broker = self.egress or EgressBroker(native)
        with BrowserFetcher(native, egress=broker) as fetcher:
            rendered = fetcher.fetch(CrawlRequest(url))
        processed = self.process_html(rendered.body.decode("utf-8", errors="replace"), rendered.final_url,
                                      status=rendered.status, config=cfg)
        processed.metadata["network_contract"] = "native_guarded"
        return processed

    def process_html(self, html: str, url: str, *, status: int = 200,
                     config: C4AConfig | None = None) -> C4AResult:
        """Pure offline dependency use; no crawl4ai browser/LLM/image fetchers."""
        from bs4 import BeautifulSoup
        from crawl4ai.markdown_generation_strategy import DefaultMarkdownGenerator
        cfg = config or self.config
        self._validate_modes(cfg)
        if len(html.encode("utf-8")) > 50_000_000:
            raise ValueError("Crawl4AI offline input exceeds budget")
        soup = BeautifulSoup(html, "lxml")
        title = soup.title.get_text(" ", strip=True) if soup.title else ""
        if cfg.excluded_selector:
            for node in soup.select(cfg.excluded_selector):
                node.decompose()
        content = "".join(str(node) for node in soup.select(cfg.css_selector)) if cfg.css_selector else str(soup)
        markdown = DefaultMarkdownGenerator().generate_markdown(content, base_url=url).raw_markdown
        extracted: dict[str, Any] = {}
        if cfg.extraction_strategy:
            extracted = {"records": cfg._build_extraction_strategy().run(url, [content])}
        return C4AResult(url=url, final_url=url, status=status, html=html, markdown=markdown,
                         text=soup.get_text(" ", strip=True), title=title, extracted=extracted,
                         links=[str(node["href"]) for node in soup.select("a[href]")],
                         metadata={"network_contract": "no_network", "processing": "offline_crawl4ai",
                                   "completeness": "unknown", "confidence": None})

    def _authorize(self, url: str, cfg: C4AConfig) -> None:
        if self.egress is not None:
            self.egress.authorize(url, purpose="browser")
        else:
            _require_target(url, cfg)

    def _record_result(self, url: str, result: C4AResult) -> None:
        if self.egress is None:
            return
        self.egress.record_response(len((result.html + result.markdown + result.text).encode("utf-8")), url=url)
        if 200 <= result.status < 300 and not result.error:
            self.egress.record_success(url)
        else:
            self.egress.record_failure(url, error=result.error or f"status={result.status}")

    def _convert(self, raw: Any) -> C4AResult:
        """将 crawl4ai CrawlResult 转换为 C4AResult。"""
        try:
            # S2.5.5：metadata 可能为 None，统一兜底空 dict 防 .get 崩溃
            metadata = getattr(raw, "metadata", None)
            metadata = metadata if isinstance(metadata, dict) else {}
            # S2.5.5：status_code 真实透传（404/403 不再兜底成 200），仅 0/None 回退 200
            status = getattr(raw, "status_code", None)
            status = status if type(status) is int and 100 <= status <= 599 else 0
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
