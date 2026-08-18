from __future__ import annotations

import copy
import json
import re
import urllib.parse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..core.config import DEFAULTS, AppConfig
from ..core.models import CrawlRequest, FetchResult
from ..core.utils import user_agent
from ..extraction.extractors import decode_body
from ..fetching.http_client import HTTPFetcher
from ..fetching.routing import needs_browser
from ..security.policy import RobotsPolicy
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def inspect_url(
    url: str,
    catalog: TemplateCatalog,
    *,
    timeout_seconds: float = 20.0,
    intent: str = "",
    fetcher: Any | None = None,
) -> SiteInspection:
    """Fetch one public page through the same SSRF/redirect/size/robots guards as a crawl.

    ``fetcher`` 传入时复用其请求通道（例如 AsyncFetcher，内部经 EgressBroker
    审计出网），否则回退为独立的 HTTPFetcher 实例。
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
        "robots_fail_closed": True,
    })
    root = Path.cwd().resolve()
    config = AppConfig(root / ".omnicrawler-inspector.yaml", root, raw, root / "work" / "site_inspection")
    if not RobotsPolicy(config).allowed(url):
        raise PermissionError("robots.txt does not allow automated inspection of this URL")
    request = CrawlRequest(url, meta={"root_url": url})
    if fetcher is not None:
        # 复用外部抓取器（须经 EgressBroker 审计），零额外连接开销
        result = fetcher.fetch(request)
    else:
        result = HTTPFetcher(config).fetch(request)
    return inspect_result(result, catalog, intent=intent)


def inspect_result(
    result: FetchResult,
    catalog: TemplateCatalog,
    *,
    limit: int = 8,
    intent: str = "",
) -> SiteInspection:
    text = decode_body(result) if "text" in result.content_type or "html" in result.content_type else ""
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

    structured: list[str] = []
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
        ("rest-json", r"/api/|/wp-json/|application/json"),
    ):
        if re.search(pattern, lower):
            api_signals.append(label)

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
    if search_form:
        page_type = "search"
    elif articles == 1 or any(marker in lower for marker in ("articlebody", "datepublished")):
        page_type = "detail"
    elif articles > 1 or links >= 20:
        page_type = "list"
    else:
        page_type = "unknown"

    browser, browser_reason = needs_browser(result)
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
    return tuple(values[:limit])


def _signals(text: str, definitions: dict[str, tuple[str, ...]]) -> tuple[str, ...]:
    return tuple(name for name, markers in definitions.items() if any(marker in text for marker in markers))


def _contains_key(value: Any, wanted: set[str]) -> bool:
    if isinstance(value, dict):
        return bool({str(key).casefold() for key in value} & wanted) or any(_contains_key(item, wanted) for item in value.values())
    if isinstance(value, list):
        return any(_contains_key(item, wanted) for item in value[:50])
    return False
