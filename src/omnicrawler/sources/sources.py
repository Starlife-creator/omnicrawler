"""链接发现与来源类型注册表。

A-P3-18 信任边界说明：``source.follow_xpath`` 等表达式来自**本地任务配置**，
按既定设计由操作者本人编写，配置文件写入即视为受信，执行时不再二次沙化。
若未来引入远程 / 市场来源任务配置，表达式必须先经 ``core/ast_evaluator`` 节点
白名单校验（钩子落点在归属外模块，见分片 A 回传报告 A-P3-18 待办），不得直接
把远程表达式送入本模块的 ``xpath`` / 提取器执行路径。
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import logging
import random
import urllib.parse
import zlib
from typing import Any
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree as SafeET

from ..core.config import AppConfig
from ..core.models import CrawlRequest, FetchResult
from ..core.pagination import body_location_payload_error
from ..core.utils import canonicalize_url
from ..extraction.extractors import decode_body, json_path
from ..extraction.html_tools import discover_links, parse_html_shared
from ..fetching.http_client import encode_request_payload

# B02-027：source.kind 单一真源。定义在 `sources/kinds.py`（**叶子模块**，无重依赖），
# 这里 re-export 供既有调用方使用。
#
# ★ 2026-11-28：定义从本文件移到叶子模块，因为 `gui/core/validator.py` 只需要
#   那 22 个字符串，却因此把本模块的 `defusedxml` / bs4 / http_client 全拖进
#   GUI 启动路径（实测 245.8 ms -> 101.9 ms）。
from .kinds import (  # noqa: F401 - re-export
    GENERIC_SOURCE_KINDS,
    SITE_ADAPTER_KINDS,
    SUPPORTED_SOURCE_KINDS,
)

LOGGER = logging.getLogger(__name__)

# B02-027：source.kind 单一真源（定义在 `sources/kinds.py`，本模块 re-export）。


class GenericSource:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.source = config.section("source")
        self.kind = config.source_kind

    def requires_discovery_body(self, request: CrawlRequest) -> bool:
        """Cursor continuation must be read every cycle, including without raw archives."""
        return request.kind != "asset" and bool(self.source.get("pagination", {}).get("next_path"))

    def seed(self) -> list[CrawlRequest]:
        if self.kind == "file" and self.source.get("local_files") is not None:
            from .local_files import seed
            return seed(self.config)
        requests: list[CrawlRequest] = []
        for raw in self.source.get("seeds", []):
            request = self._seed_request(raw)
            if request is not None:
                requests.append(request)
        pagination = self.source.get("pagination", {})
        if pagination.get("type") == "page" and requests:
            start = int(pagination.get("start", 1))
            end = int(pagination.get("end", start))
            step = max(1, int(pagination.get("step", 1)))
            name = str(pagination.get("parameter", "page"))
            # S2.5.7：每个 seed 请求都进入分页逻辑，不再只保留 requests[0]
            paged: list[CrawlRequest] = []
            for template in requests:
                for page in range(start, end + 1, step):
                    url = _with_query(template.url, {name: page})
                    body = template.body
                    if template.method == "POST" and pagination.get("location") == "body":
                        # ★ 2026-09-30 修：此前是 `dict(self.source.get("payload", {}))` ⇒
                        #   ① payload 是字符串/数字等**非映射真值**时抛裸 `ValueError`
                        #      （`dict("abc")`，没有任何上下文）；
                        #   ② `payload:`（显式 null）时 `get` 返回 None ⇒ 裸 `TypeError`。
                        #   现在：空值一律当"没有请求体"（`or {}`），非映射真值给**指名道姓**的
                        #   报错；判据与文案的唯一真源＝`core.pagination.body_location_payload_error`
                        #   （同一判据也在 `validate_config` 里做启动前校验 ⇒ 正常情况下到不了这里）。
                        payload = self.source.get("payload") or {}
                        payload_issue = body_location_payload_error(payload)
                        if payload_issue:
                            raise ValueError(payload_issue)
                        payload = dict(payload)
                        payload[name] = page
                        body, _ = encode_request_payload(template.method, payload, "application/json")
                    paged.append(CrawlRequest(
                        url, template.method, dict(template.headers), body, template.kind,
                        template.render, template.priority, 0, None,
                        {**template.meta, "page": page},
                    ))
            requests = paged
        return requests

    def _seed_request(self, raw: Any) -> CrawlRequest | None:
        if isinstance(raw, dict):
            url = str(raw["url"])
            method = str(raw.get("method", "GET")).upper()
            headers = {str(k): str(v) for k, v in raw.get("headers", {}).items()}
            body, payload_headers = encode_request_payload(method, raw.get("payload"), str(raw.get("content_type", "application/json")))
            headers = {**payload_headers, **headers}
            return CrawlRequest(url, method, headers, body, str(raw.get("kind", "page")), bool(raw.get("render", False)), meta={"root_url": url})
        url = str(raw)
        # N2：url_list 导入源做状态化清洗，区分「协议不受支持」与「无法识别」，
        # 不再静默丢弃（采集链路仅支持 http/https）
        if self.kind == "url_list":
            from .url_cleaner import clean_url_status

            cleaned, status = clean_url_status(url)
            if cleaned is None:
                if status.startswith("unsupported:"):
                    LOGGER.warning("url_list 跳过不受支持的协议 seed: %.80r（%s）", url, status)
                elif status == "no_scheme":
                    LOGGER.warning("url_list 跳过无法识别的 seed: %.80r", url)
                return None
            url = cleaned
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
        if self.kind == "file" and self.source.get("local_files") is not None:
            return []
        if self.kind in {"sitemap", "feed"}:
            return self._discover_xml(result)
        discovered: list[CrawlRequest] = []
        result.meta["discovery_filtered_links"] = []
        if "html" in result.content_type:
            # ★ 2026-11-27：共享解析（只读契约）—— 提取阶段刚刚解析过同一份字节。
            document = parse_html_shared(decode_body(result))
            # browser 也参与链接发现：浏览器源同样受 crawl.max_pages / max_depth /
            # same_host 约束（既有模板 authenticated/cookie-session、
            # generic/infinite-scroll 都声明了 max_depth），把它排除在外会让
            # "用浏览器抓分页列表"根本无法翻页——分析器只好退化成"点击下一页"
            # 动作，而该动作在入口页也执行，反而毁掉第一页。
            can_crawl = self.kind in {"crawl", "focused", "incremental", "media", "browser"}
            download = self.config.section("download")
            extensions = tuple(str(item).lower() for item in download.get("extensions", []))
            # A-P3-18：follow_xpath 仅接受本地受信任务配置；远程/市场来源表达式
            # 须先过 core/ast_evaluator 白名单校验（归属外钩子，待集成步骤落地）。
            follow_xpath = str(self.source.get("follow_xpath") or "").strip()
            allowed_links = None
            if follow_xpath:
                from lxml import html as lxml_html

                root = lxml_html.fromstring(decode_body(result))
                allowed_links = {str(node.get("href")).strip() for node in root.xpath(follow_xpath)
                                 if hasattr(node, "get") and node.get("href")}
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
                    child = self._child(result, url, "page", label)
                    if allowed_links is None or href.strip() in allowed_links:
                        discovered.append(child)
                    else:
                        result.meta["discovery_filtered_links"].append({
                            "fingerprint": child.fingerprint, "url": child.url, "kind": child.kind,
                            "depth": child.depth, "decision": "follow_filter", "reason": "source.follow_xpath",
                        })
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
            # 继承父请求的 render：浏览器源发现的子页必须同样渲染。否则同一页在
            # "种子（渲染）"与"子链接（HTTP）"两条路径下产出不同字节 → 内容哈希
            # 不同 → 去重失效、同一页被重复采集（实测 scrapethissite：apex 种子
            # 重定向到 www 后，页面自链接又被当新页抓一次 → 250 条变 500 条），
            # 且 JS 渲染的分页页（quotes.toscrape.com/js/page/N）拿不到内容。
            render=result.request.render,
            parent_url=result.final_url,
            meta={"root_url": result.request.meta.get("root_url", result.request.url), "anchor": label},
        )

    def _discover_xml(self, result: FetchResult) -> list[CrawlRequest]:
        try:
            # B08-003：sitemap/feed 解析用 defusedxml（禁 DTD/外部实体，防 XXE/billion-laughs）
            root = SafeET.fromstring(_xml_payload(result))
        except (ParseError, ValueError):
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
        diagnostic: dict[str, Any] = {"pagination_kind": "cursor", "pages_seen": result.request.depth + 1}
        result.meta["pagination_diagnostic"] = diagnostic
        parameter = str(pagination.get("parameter", "")).strip()
        current = urllib.parse.parse_qs(urllib.parse.urlsplit(result.request.url).query).get(parameter, [])
        diagnostic["cursor_sha256"] = hashlib.sha256(json.dumps(current).encode()).hexdigest()
        try:
            values = json_path(json.loads(decode_body(result)), str(next_path))
        except (ValueError, TypeError):
            diagnostic["stop_reason"] = "invalid_pagination_response"
            return []
        if not values or values[0] is None or values[0] == "" or values[0] is False:
            diagnostic["stop_reason"] = "no_next_value"
            return []
        if type(values[0]) not in {str, int, float}:
            diagnostic["stop_reason"] = "unsupported_cursor_type"
            raise ValueError("下一页游标必须是标量；不能确认已完整遍历")
        next_value = str(values[0])
        diagnostic["next_cursor_sha256"] = hashlib.sha256(json.dumps(values[0]).encode()).hexdigest()
        url = (_replace_query(result.request.url, {parameter: next_value}) if parameter
               else canonicalize_url(result.final_url, next_value) or "")
        if not url:
            diagnostic["stop_reason"] = "invalid_next_url"
            raise ValueError("下一页地址无效；不能确认已完整遍历")
        child = CrawlRequest(
            url, method=result.request.method, headers=dict(result.request.headers),
            body=result.request.body, kind=result.request.kind, render=result.request.render,
            priority=result.request.priority, depth=result.request.depth + 1,
            parent_url=result.final_url,
            meta={**result.request.meta, "_api_pagination_generated": True},
        )
        seen = list(result.request.meta.get("_api_pagination_seen", []))
        if result.request.fingerprint not in seen:
            seen.append(result.request.fingerprint)
        if child.fingerprint in seen:
            diagnostic["stop_reason"] = "repeated_continuation"
            raise ValueError("分页重复返回已访问的游标/地址；不能确认已完整遍历")
        if len(seen) >= 2000:
            diagnostic["stop_reason"] = "pagination_history_limit"
            raise ValueError("分页跟踪达到上限；不能确认已完整遍历")
        child.meta["_api_pagination_seen"] = seen
        diagnostic["stop_reason"] = "next_request_generated"
        return [child]


#: sitemap 允许的最大解压体积。sitemaps.org 规定单文件解压后不超过 50 MB；
#: 这里取同一个数字，既符合规范又挡住 zip-bomb。
_SITEMAP_MAX_DECOMPRESSED = 50 * 1024 * 1024

#: `.gz` 扩展名 / gzip 魔数 —— 两者任一成立就按 gzip 处理。
#: ★ 用**魔数**而不只看扩展名：站点常把 `sitemap.xml.gz` 的内容以
#:   `application/octet-stream` 直接返回，扩展名也可能被 CDN 去掉。
_GZIP_MAGIC = b"\x1f\x8b"


def _xml_payload(result: FetchResult) -> bytes:
    """取出可解析的 XML 字节 —— **透明处理 gzip 压缩的 sitemap**。

## 为什么必须支持

sitemaps.org 明确允许 `sitemap.xml.gz`（大站的地图动辄几十 MB，gzip 是标准做法）。
实测 2026-11-27 一个 `sitemap.xml.gz` 返回 `[]` —— 也就是说：

* `templates/protocols/sitemap` 模板对这类站点**一条 URL 都发现不了**；
* 而它既不报错也不告警，用户只会觉得"这个站没内容"。

## 安全边界

* 解压**限流**（`_SITEMAP_MAX_DECOMPRESSED`，与 sitemaps.org 的 50 MB 上限一致）：
  压缩炸弹会在这里被拒绝，而不是把内存吃光；
* 解压后仍走 `defusedxml`（禁 DTD / 外部实体），XXE 防护不因解压而失效；
* 解压失败**返回原始字节**让后续解析自然失败 —— 不静默猜测。
"""
    body = result.body
    if not body:
        return body
    # ★ 2026-11-27 实测缺陷：`<?xml ...?>` 前面**多一个换行或 BOM** 就会让 Expat 报
    #   「XML or text declaration not at start of entity」⇒ 整个 sitemap 解析失败 ⇒
    #   返回 [] ⇒ 用户看到"这个站没内容"。
    #
    #   真实站点真的这么发：web-scraping.dev 的 sitemap.xml 第一个字节就是 `\n`。
    #   规范要求声明在文件最开头，但**我们能容忍的就不该让用户承担**——
    #   这里去掉开头的空白与 UTF-8 BOM，其余字节原样交给 defusedxml。
    body = body.lstrip(b"\xef\xbb\xbf").lstrip()
    is_gzip = body[:2] == _GZIP_MAGIC or str(result.final_url).casefold().endswith(".gz")
    if not is_gzip:
        return body
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as handle:
            # 多读 1 字节用于判断"是否超限"，避免先解压再测量
            payload = handle.read(_SITEMAP_MAX_DECOMPRESSED + 1)
    except (OSError, EOFError, zlib.error):
        LOGGER.warning("sitemap 声称为 gzip 但解压失败，按原始字节继续: %s", result.final_url)
        return body
    if len(payload) > _SITEMAP_MAX_DECOMPRESSED:
        LOGGER.warning("sitemap 解压后超过上限（%d 字节），已拒绝: %s", _SITEMAP_MAX_DECOMPRESSED, result.final_url)
        return b""
    return payload


def _with_query(url: str, params: dict[str, Any]) -> str:
    parts = urllib.parse.urlsplit(url)
    current = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    current.extend((str(k), str(item)) for k, value in params.items() for item in (value if isinstance(value, list) else [value]))
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(current), parts.fragment))


def _replace_query(url: str, params: dict[str, Any]) -> str:
    """替换指定查询参数，同时保留其它参数、重复值和片段。"""
    parts = urllib.parse.urlsplit(url)
    replacing = {str(key) for key in params}
    current = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        if key not in replacing
    ]
    current.extend(
        (str(key), str(item))
        for key, value in params.items()
        for item in (value if isinstance(value, list) else [value])
    )
    query = urllib.parse.urlencode(current)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


def register(registry) -> None:
    for name in GENERIC_SOURCE_KINDS:
        registry.register_source(name, GenericSource)
