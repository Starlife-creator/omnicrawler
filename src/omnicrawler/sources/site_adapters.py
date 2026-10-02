from __future__ import annotations

import json
import urllib.parse
from typing import Any

from ..core.models import CrawlRequest, FetchResult
from ..core.utils import canonicalize_url
from ..extraction.extractors import decode_body, json_path
from ..plugins.plugins import PluginMetadata
from .sources import GenericSource, _replace_query


class DynamicApiSource(GenericSource):
    """Base for APIs whose server response determines the next request."""

    def seed(self) -> list[CrawlRequest]:
        seeds = [
            request for raw in self.source.get("seeds", [])
            if (request := self._seed_request(raw)) is not None
        ]
        for request in seeds:
            request.meta["page"] = self._page(request.url)
        return seeds

    @staticmethod
    def _page(url: str) -> int:
        values = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("page", ["1"])
        try:
            return int(values[0])
        except (TypeError, ValueError):
            return 1


    @staticmethod
    def _next_request(result: FetchResult, url: str, **metadata: Any) -> list[CrawlRequest]:
        seen = [*result.request.meta.get("_cms_pagination_seen", []),
                result.request.url, result.final_url]
        if url in seen:
            raise ValueError("CMS pagination repeated a continuation URL; collection is incomplete")
        request = result.request
        return [CrawlRequest(
            url, method=request.method, headers=dict(request.headers), body=request.body,
            kind=request.kind, render=request.render, priority=request.priority,
            depth=request.depth + 1, parent_url=result.final_url,
            meta={**request.meta, **metadata, "_cms_pagination_seen": list(dict.fromkeys(seen))},
        )]


class WordPressSource(DynamicApiSource):
    def discover(self, result: FetchResult) -> list[CrawlRequest]:
        total_raw = result.headers.get("x-wp-totalpages", "0")
        try:
            total = int(total_raw)
        except (TypeError, ValueError):
            return []
        current = int(result.request.meta.get("page", self._page(result.final_url)))
        limit = int(self.source.get("max_pages", total))
        if current >= min(total, limit):
            return []
        url = _replace_query(result.final_url, {"page": current + 1})
        return self._next_request(result, url, page=current + 1)


class DrupalJsonApiSource(DynamicApiSource):
    def discover(self, result: FetchResult) -> list[CrawlRequest]:
        try:
            values = json_path(json.loads(decode_body(result)), "links.next.href")
        except (TypeError, ValueError):
            return []
        if not values or not values[0]:
            return []
        url = canonicalize_url(result.final_url, str(values[0]))
        return [CrawlRequest(url, headers=dict(result.request.headers), meta=result.request.meta)] if url else []


class MediaWikiSource(DynamicApiSource):
    def discover(self, result: FetchResult) -> list[CrawlRequest]:
        try:
            payload = json.loads(decode_body(result))
        except (TypeError, ValueError):
            return []
        continuation = payload.get("continue", {})
        if not isinstance(continuation, dict) or not continuation:
            return []
        parameters = {str(key): value for key, value in continuation.items()}
        # Remove only continuation keys carried by our preceding request. Query
        # filters, including repeated values, remain intact when token names change.
        replacing: dict[str, Any] = {key: [] for key in result.request.meta.get("_cms_continuation_keys", [])}
        replacing.update(parameters)
        url = _replace_query(result.final_url, replacing)
        return self._next_request(result, url, _cms_continuation_keys=list(parameters))


class DiscourseSource(DynamicApiSource):
    def discover(self, result: FetchResult) -> list[CrawlRequest]:
        try:
            payload: Any = json.loads(decode_body(result))
            values = json_path(payload, "topic_list.more_topics_url")
        except (TypeError, ValueError):
            return []
        if not values or not values[0]:
            return []
        url = canonicalize_url(result.final_url, str(values[0]))
        return [CrawlRequest(url, headers=dict(result.request.headers), meta=result.request.meta)] if url else []


def register(registry) -> None:
    from .sources import SITE_ADAPTER_KINDS

    adapters = {
        "site_wordpress": WordPressSource,
        "site_drupal": DrupalJsonApiSource,
        "site_mediawiki": MediaWikiSource,
        "site_discourse": DiscourseSource,
    }
    for name in SITE_ADAPTER_KINDS:
        registry.register_source(name, adapters[name])
    registry.plugins.append(PluginMetadata(
        name="builtin-site-adapters",
        version="1.0.0",
        description="Official-API adapters for WordPress, Drupal, MediaWiki and Discourse",
        plugin_types=("source",),
        capabilities=("server-driven-pagination", "continuation-token", "cms-detection"),
        domains=("*",),
        license="MIT",
        source_url="https://github.com/omnicrawler/omnicrawler",
        fallback="rest",
        resource_limits={"max_concurrency": 8},
    ))
