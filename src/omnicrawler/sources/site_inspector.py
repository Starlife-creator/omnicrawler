from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from ..core.models import FetchResult
from ..extraction.extractors import decode_body
from ..templates.template_catalog import TemplateCatalog, TemplateMatch, TemplateProbe


@dataclass(frozen=True, slots=True)
class SiteInspection:
    url: str
    content_type: str
    page_type: str
    dynamic: bool
    browser_recommended: bool
    browser_reason: str
    cms: tuple[str, ...]
    frameworks: tuple[str, ...]
    pagination: tuple[str, ...]
    structured_data: tuple[str, ...]
    api_signals: tuple[str, ...]
    downloads: tuple[str, ...]
    authentication: tuple[str, ...]
    recommendations: tuple[dict[str, Any], ...]
    rendered: bool = False
    analysis_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def fetch_analysis_page(url: str, **kwargs: Any) -> tuple[FetchResult, bool, str]:
    """Shared analysis eligibility with I/O kept below the extraction layer."""
    from ..extraction.intelligent_scraper import _analysis_browser_reason
    from ..fetching.page_probe import fetch_analysis_page as fetch

    return fetch(url, browser_reason=_analysis_browser_reason, **kwargs)


def fetch_page_html(
    url: str,
    *,
    timeout_seconds: float = 20.0,
    robots_fail_closed: bool = True,
    allow_private_network: bool = False,
    fetcher: Any | None = None,
) -> tuple[str, str]:
    """抓一页 HTML，返回 ``(html, final_url)`` —— **守卫与 crawl 完全一致**。

    供 GUI 的「分析页面并填字段」使用：它必须看到与真实运行**同一份内容**，
    否则会出现"分析说得通、运行跑不通"。
    """
    result, _rendered, _error = fetch_analysis_page(
        url, timeout_seconds=timeout_seconds, robots_fail_closed=robots_fail_closed,
        allow_private_network=allow_private_network, fetcher=fetcher,
    )
    if _error:
        raise RuntimeError(_error)
    return decode_body(result), (result.final_url or url)


def inspect_url(
    url: str,
    catalog: TemplateCatalog,
    *,
    timeout_seconds: float = 20.0,
    intent: str = "",
    robots_fail_closed: bool = True,  # P2-8：默认 fail-closed，可联动用户配置
    fetcher: Any | None = None,
    allow_private_network: bool = False,
) -> SiteInspection:
    """Fetch one public page through the same SSRF/redirect/size/robots guards as a crawl.

    ``fetcher`` 传入时复用其请求通道（例如 AsyncFetcher，内部经 EgressBroker
    审计出网），否则回退为独立的 HTTPFetcher 实例。
    """
    result, rendered, error = fetch_analysis_page(
        url, timeout_seconds=timeout_seconds, robots_fail_closed=robots_fail_closed,
        allow_private_network=allow_private_network, fetcher=fetcher,
    )
    report = inspect_result(result, catalog, intent=intent)
    if rendered:
        report = replace(report, dynamic=True, browser_recommended=True,
                         browser_reason="静态内容不足，已通过浏览器获得业务页面", rendered=True)
    return replace(report, analysis_error=error)


def inspect_result(
    result: FetchResult,
    catalog: TemplateCatalog,
    *,
    limit: int = 8,
    intent: str = "",
) -> SiteInspection:
    text = decode_body(result) if any(kind in result.content_type for kind in ("text", "html", "json")) else ""
    lower = text.casefold()
    parsed_json: Any = None
    if "json" in result.content_type:
        try:
            parsed_json = json.loads(text)
        except (TypeError, ValueError):
            parsed_json = None

    cms = _signals(lower, {
        "wordpress": ("/wp-content/", "wp-json"),
        "drupal": ("drupal.settings", "/core/misc/drupal.js"),
        "mediawiki": ("mw.config", "/w/load.php"),
        "discourse": ("discourse-application", "discourse_theme_id"),
        "discuz": ("discuz_uid", "forum.php"),
        "shopify": ("shopify.theme", "/cdn/shop/"),
    })
    frameworks = _signals(lower, {
        "react/next": ("__next_data__", "data-reactroot", "id=\"__next\""),
        "vue/nuxt": ("__nuxt__", "data-v-", "id=\"__nuxt\""),
        "angular": ("ng-version", "app-root"),
        "svelte": ("__svelte", "sveltekit"),
    })
    pagination: list[str] = []
    if re.search(r"rel=['\"]next|class=['\"][^'\"]*(?:next|pagination)", lower):
        pagination.append("next-link")
    if re.search(r"[?&](?:page|p|offset)=\d+", lower):
        pagination.append("numbered-or-offset")
    if any(marker in lower for marker in ("intersectionobserver", "infinite-scroll", "load-more")):
        pagination.append("infinite-scroll")
    if parsed_json is not None and _contains_key(parsed_json, {"next", "cursor", "continuation", "has_next_page"}):
        pagination.append("cursor-or-next-url")

    # ★ 2026-11-27：页面里若出现 sitemap 链接/声明，说明这个站有**全站发现**入口。
    #   对"没识别出列表"的站点，这是最实用的一条下一步（`protocols/sitemap` 模板）。
    sitemap_hint = bool(
        re.search(r"sitemap[^\"'<\s]{0,40}\.xml", text, re.IGNORECASE)
    )

    structured: list[str] = []
    # ★ 2026-11-27：**页面内嵌的 SSR 数据**是一种独立的数据来源，不是"动态渲染"的旁证。
    #   实测：Next.js / Nuxt 站点把整页数据放在 __NEXT_DATA__ 里，DOM 只有一个空挂载点 ——
    #   旧实现只报 `dynamic=True`，于是推荐"开浏览器"，而**静态抓取就能拿全**。
    #   这条信号让 `auto-analyze` / `templates recommend` 能把人引到正确的那条路上。
    if any(marker in lower for marker in ("__next_data__", "__nuxt_data__", "__initial_state__", "__apollo_state__")):
        structured.append("embedded-json")
    if sitemap_hint:
        structured.append("sitemap")
    if "application/ld+json" in lower:
        structured.append("json-ld")
    if "property=\"og:" in lower or "property='og:" in lower:
        structured.append("opengraph")
    if "twitter:card" in lower:
        structured.append("twitter-card")
    if re.search(r"<(?:table)\b", lower):
        structured.append("html-table")

    api_signals: list[str] = []
    for label, pattern in (
        ("fetch", r"\bfetch\s*\("),
        ("xhr", r"xmlhttprequest|\.ajax\s*\("),
        ("graphql", r"/graphql\b|\bgraphql\b"),
        # ★ 2026-11-27 修：`application/json` **不能单独**当 API 信号。
        #
        #   事故实测：一个讲解 HTTP 头的**文档页**，正文里写着
        #   「Send Content-Type: application/json to the server」，
        #   于是被判成有 background API，推荐第一位变成
        #   `generic/spa-api-discovery`（「开浏览器去抓 API」）——
        #   而那个页面根本没有 API。
        #
        #   真正的 API 信号是**路径形态**（`/api/`、`/wp-json/`）或**确实有调用**
        #   （`fetch(` / `xhr` / `graphql`）。`application/json` 只作为
        #   「有路径信号时的补充确认」，见下面第二条判据。
        ("rest-json", r"/api/|/wp-json/"),
    ):
        if re.search(pattern, lower):
            api_signals.append(label)
    # 路径信号之外，`application/json` **配合**任一真实调用才算 API 页 ——
    # 单凭字面量出现（文档、代码示例、content-type 说明）不构成调用。
    if "rest-json" not in api_signals and api_signals and "application/json" in lower:
        api_signals.append("rest-json")

    asset_candidates = re.findall(r"(?:href|src)\s*=\s*['\"]([^'\"]+)['\"]", text, re.IGNORECASE)
    allowed_extensions = {"pdf", "doc", "docx", "xls", "xlsx", "csv", "zip", "ppt", "pptx", "json"}
    downloads = tuple(sorted({
        extension
        for value in asset_candidates
        for extension in [Path(urllib.parse.urlsplit(value).path).suffix.casefold().lstrip(".")]
        if extension in allowed_extensions
    }))
    auth: list[str] = []
    if re.search(r"<input\b[^>]+type=['\"]password", lower):
        auth.append("password-form")
    if any(marker in lower for marker in ("oauth", "openid", "saml")):
        auth.append("federated-login")

    articles = len(re.findall(r"<article\b", lower))
    links = len(re.findall(r"<a\b", lower))
    search_form = bool(re.search(r"<form\b[^>]*(?:search|query)|<input\b[^>]+type=['\"]search", lower))
    # ★ 2026-11-27 修：**有搜索框 ≠ 这是搜索结果页**。
    #
    #   事故实测：一个 20 张卡片的商品列表页，顶部有最常见的导航搜索框
    #   （`<form action="/search"><input name="q"></form>`）⇒ 旧判据把整页判成
    #   `page_type = "search"` ⇒ 推荐 `generic/single-page`（**单页**模板，不翻页、
    #   不进详情）；去掉那个搜索框同一页就判成 `list` ⇒ 推荐 `generic/list-detail`。
    #   也就是一个导航控件把整条采集策略带偏了。
    #
    #   判据：搜索框只在**页面主体不是列表**时才决定类型。列表信号（重复文章块或
    #   足够多的链接）优先 —— 那才是"这页是干什么的"。
    list_like = articles > 1 or links >= 20
    if search_form and not list_like:
        page_type = "search"
    elif articles == 1 or any(marker in lower for marker in ("articlebody", "datepublished")):
        page_type = "detail"
    elif list_like:
        page_type = "list"
    else:
        page_type = "unknown"

    from ..extraction.intelligent_scraper import _analysis_browser_reason

    browser_reason = _analysis_browser_reason(result)
    browser = bool(browser_reason)
    script_count = len(re.findall(r"<script\b", lower))
    dynamic = browser or bool(frameworks) or script_count >= 12
    matches = catalog.recommend(
        TemplateProbe(result.final_url, result.headers, text, parsed_json), limit=limit, intent=intent
    )
    recommendations = _recommendations(
        matches, page_type, dynamic, bool(downloads), structured, limit, intent=intent,
        api_signals=api_signals,
    )
    return SiteInspection(
        result.final_url,
        result.content_type,
        page_type,
        dynamic,
        browser,
        browser_reason,
        cms,
        frameworks,
        tuple(dict.fromkeys(pagination)),
        tuple(structured),
        tuple(api_signals),
        downloads,
        tuple(auth),
        recommendations,
    )


def _recommendations(
    matches: list[TemplateMatch],
    page_type: str,
    dynamic: bool,
    has_downloads: bool,
    structured: list[str],
    limit: int,
    *,
    intent: str = "",
    api_signals: list[str] | None = None,
) -> tuple[dict[str, Any], ...]:
    values: list[dict[str, Any]] = [
        {"id": match.record.metadata.template_id, "score": match.score, "reasons": match.reasons}
        for match in matches
    ]
    fallbacks: list[tuple[str, str]] = []
    if intent == "documents" and dynamic:
        fallbacks.append(("recipes/dynamic-topic-pdf-monitor", "intent:documents+dynamic"))
    if intent == "documents":
        fallbacks.append(("documents/pdf-collection", "intent:documents"))
    if intent == "updates":
        fallbacks.append(("incremental", "intent:updates"))
    if api_signals and dynamic:
        fallbacks.append(("generic/spa-api-discovery", "background-api-signals"))
    # ★ 2026-11-27：内嵌数据优先于"开浏览器" —— 这类页面静态即可取全，
    #   推荐浏览器等于让用户白付一个数量级的成本。
    if "embedded-json" in structured:
        fallbacks.append(("generic/ssr-embedded-data", "embedded-json"))
    # ★ 2026-11-27：站点声明了 sitemap ⇒ 全站发现最省事的入口。
    #   对"没识别出列表"的页面，这是最实用的一条下一步。
    if "sitemap" in structured:
        fallbacks.append(("protocols/sitemap", "sitemap-declared"))
    if dynamic:
        fallbacks.append(("generic/infinite-scroll", "dynamic-page"))
    if has_downloads:
        fallbacks.append(("generic/attachments", "download-links"))
    if "html-table" in structured:
        fallbacks.append(("generic/html-table", "html-table"))
    if page_type == "list":
        fallbacks.append(("generic/list-detail", "list-page"))
    else:
        fallbacks.append(("generic/single-page", "safe-generic-fallback"))
    seen = {item["id"] for item in values}
    for template_id, reason in fallbacks:
        if template_id not in seen:
            values.append({"id": template_id, "score": 10, "reasons": (reason,)})
            seen.add(template_id)
    # ★ 2026-11-27：**内嵌数据是这类页面的最省路径，必须排第一**。
    #
    #   实测：`__NEXT_DATA__` 页面上，`generic/spa-api-discovery` 与
    #   `generic/ssr-embedded-data` 都靠 `html_contains` 各得 15 分，同分时按
    #   template_id 字典序 —— 于是"开浏览器做 API 发现"排在"静态取内嵌数据"前面。
    #   对用户来说这就是"先花一个数量级的时间做错的事"。
    #
    #   判据是"这条路径更省且同样正确"，不是"这个模板更好"：没有内嵌载荷时
    #   该模板根本不会被加入（上面 fallbacks 的条件）。
    if "embedded-json" in structured:
        values.sort(key=lambda item: 0 if item["id"] == "generic/ssr-embedded-data" else 1)
    return tuple(values[:limit])


def _signals(text: str, definitions: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    return tuple(name for name, markers in definitions.items() if any(marker in text for marker in markers))


def _contains_key(value: Any, wanted: set[str]) -> bool:
    if isinstance(value, dict):
        return bool({str(key).casefold() for key in value} & wanted) or any(_contains_key(item, wanted) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, wanted) for item in value[:50])
    return False
