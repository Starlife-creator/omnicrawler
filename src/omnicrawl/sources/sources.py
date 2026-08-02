from __future__ import annotations

import json
import random
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Any

from ..core.config import AppConfig
from ..core.models import CrawlRequest, FetchResult
from ..core.utils import canonicalize_url
from ..extraction.extractors import decode_body, json_path
from ..extraction.html_tools import discover_links, parse_html
from ..fetching.http_client import encode_request_payload


class GenericSource:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.source = config.section("source")
        self.kind = config.source_kind

    def seed(self) -> list[CrawlRequest]:
        requests: list[CrawlRequest] = []
        for raw in self.source.get("seeds", []):
            request = self._seed_request(raw)
            requests.append(request)
        pagination = self.source.get("pagination", {})
        if pagination.get("type") == "page" and requests:
            start = int(pagination.get("start", 1))
            end = int(pagination.get("end", start))
            step = max(1, int(pagination.get("step", 1)))
            name = str(pagination.get("parameter", "page"))
            template = requests[0]
            requests = []
            for page in range(start, end + 1, step):
                url = _with_query(template.url, {name: page})
                body = template.body
                if template.method == "POST" and pagination.get("location") == "body":
                    payload = dict(self.source.get("payload", {}))
                    payload[name] = page
                    body, _ = encode_request_payload(template.method, payload, "application/json")
                requests.append(CrawlRequest(
                    url, template.method, dict(template.headers), body, template.kind,
                    template.render, template.priority, 0, None,
                    {**template.meta, "page": page},
                ))
        return requests

    def _seed_request(self, raw: Any) -> CrawlRequest:
        if isinstance(raw, dict):
            url = str(raw["url"])
            method = str(raw.get("method", "GET")).upper()
            headers = {str(k): str(v) for k, v in raw.get("headers", {}).items()}
            body, payload_headers = encode_request_payload(method, raw.get("payload"), str(raw.get("content_type", "application/json")))
            headers = {**payload_headers, **headers}
            return CrawlRequest(url, method, headers, body, str(raw.get("kind", "page")), bool(raw.get("render", False)), meta={"root_url": url})
        url = str(raw)
        method = str(self.source.get("method", "GET")).upper()
        headers = {str(k): str(v) for k, v in self.source.get("headers", {}).items()}
        render = self.kind == "browser"
        body = None
        if self.kind == "rest":
            url = _with_query(url, self.source.get("params", {}))
            body, payload_headers = encode_request_payload(method, self.source.get("payload"), str(self.source.get("content_type", "application/json")))
            headers = {**payload_headers, **headers}
        elif self.kind == "graphql":
            query = str(self.source.get("query", ""))
            query_file = self.source.get("query_file")
            if query_file:
                path = self.config.resolve(query_file)
                query = path.read_text(encoding="utf-8")
            body, payload_headers = encode_request_payload("POST", {"query": query, "variables": self.source.get("variables", {})}, "application/json")
            method, headers = "POST", {**payload_headers, **headers}
        elif self.kind == "form":
            body, payload_headers = encode_request_payload(method, self.source.get("fields", {}), "application/x-www-form-urlencoded")
            headers = {**payload_headers, **headers}
        return CrawlRequest(
            url=url, method=method, headers=headers, body=body,
            kind="asset" if self.kind == "file" else "page", render=render,
            meta={"root_url": url, "source_kind": self.kind},
        )

    def discover(self, result: FetchResult) -> list[CrawlRequest]:
        if self.kind in {"sitemap", "feed"}:
            return self._discover_xml(result)
        discovered: list[CrawlRequest] = []
        if "html" in result.content_type:
            document = parse_html(decode_body(result))
            can_crawl = self.kind in {"crawl", "focused", "incremental", "media"}
            download = self.config.section("download")
            extensions = tuple(str(item).lower() for item in download.get("extensions", []))
            for href, label, link_kind in discover_links(document):
                url = canonicalize_url(result.final_url, href)
                if not url:
                    continue
                path = urllib.parse.urlsplit(url).path.lower()
                is_attachment = bool(extensions and path.endswith(extensions))
                is_media = link_kind == "media"
                if is_attachment and download.get("enabled"):
                    discovered.append(self._child(result, url, "asset", label))
                elif is_media and (download.get("media") or self.kind == "media"):
                    discovered.append(self._child(result, url, "asset", label))
                elif can_crawl and link_kind == "link":
                    discovered.append(self._child(result, url, "page", label))
        if self.kind in {"rest", "graphql"}:
            discovered.extend(self._discover_api_next(result))
        return discovered

    def _child(self, result: FetchResult, url: str, kind: str, label: str) -> CrawlRequest:
        keywords = [str(item).casefold() for item in self.config.section("crawl").get("focus_keywords", [])]
        score = sum(1 for word in keywords if word in f"{url} {label}".casefold())
        priority: float
        strategy = self.config.section("crawl").get("strategy", "bfs")
        if strategy == "dfs":
            priority = result.request.depth + 1
        elif strategy == "random":
            priority = random.random()
        else:
            priority = float(score)
        return CrawlRequest(
            url=url, kind=kind, priority=priority, depth=result.request.depth + 1,
            parent_url=result.final_url,
            meta={"root_url": result.request.meta.get("root_url", result.request.url), "anchor": label},
        )

    def _discover_xml(self, result: FetchResult) -> list[CrawlRequest]:
        try:
            root = ET.fromstring(result.body)
        except ET.ParseError:
            return []
        urls: list[str] = []
        if self.kind == "sitemap":
            for node in root.iter():
                if node.tag.rsplit("}", 1)[-1].lower() == "loc" and node.text:
                    urls.append(node.text.strip())
        else:
            for node in root.iter():
                name = node.tag.rsplit("}", 1)[-1].lower()
                if name == "link":
                    value = node.attrib.get("href") or (node.text or "")
                    if value.strip():
                        urls.append(value.strip())
        requests: list[CrawlRequest] = []
        for value in dict.fromkeys(urls):
            url = canonicalize_url(result.final_url, value)
            if url:
                requests.append(CrawlRequest(
                    url, depth=result.request.depth + 1, parent_url=result.final_url,
                    meta={"root_url": result.request.meta.get("root_url", result.request.url)},
                ))
        return requests

    def _discover_api_next(self, result: FetchResult) -> list[CrawlRequest]:
        pagination = self.source.get("pagination", {})
        next_path = pagination.get("next_path")
        if not next_path:
            return []
        try:
            values = json_path(json.loads(decode_body(result)), str(next_path))
        except (ValueError, TypeError):
            return []
        if not values or not values[0]:
            return []
        next_value = str(values[0])
        parameter = str(pagination.get("parameter", "")).strip()
        if parameter:
            url = _with_query(result.request.url, {parameter: next_value})
        else:
            url = canonicalize_url(result.final_url, next_value) or ""
        if not url:
            return []
        return [CrawlRequest(
            url, method=result.request.method, headers=dict(result.request.headers),
            body=result.request.body, meta=result.request.meta,
        )]


def _with_query(url: str, params: dict[str, Any]) -> str:
    parts = urllib.parse.urlsplit(url)
    current = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    current.extend((str(k), str(item)) for k, value in params.items() for item in (value if isinstance(value, list) else [value]))
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(current), parts.fragment))


def register(registry) -> None:
    for name in (
        "static_html", "crawl", "focused", "incremental", "url_list", "rest",
        "graphql", "form", "sitemap", "feed", "browser", "file", "media",
        "websocket", "sse", "long_poll", "redis", "scrapy",
    ):
        registry.register_source(name, GenericSource)
