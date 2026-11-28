"""`source.kind` 白名单的**叶子模块**（2026-11-28）。

## 为什么单独拆出来

`sources/sources.py` 是个重模块：它导入 `defusedxml`、`extraction.extractors`、
`extraction.html_tools`（连带 **bs4**，约 74 ms）、`fetching.http_client`。

而 `gui/core/validator.py` 只为了用**一个常量** `SUPPORTED_SOURCE_KINDS` 就把它
整个拉进 GUI 启动路径 —— 实测 `gui.core.validator` 导入要 **115.9 ms**，
其中绝大部分是这条向下拖出来的链。

## 判据

白名单是**纯数据**（18 + 4 个字符串元组），不含任何行为。它属于叶子模块，
不该被一个「会联网、会解析 XML、会解码 HTML」的模块垄断。

★ 单一真源不变：`sources/sources.py` 从这里 **re-export**，所以
  `from omnicrawler.sources.sources import SUPPORTED_SOURCE_KINDS` 照旧可用，
  既有调用方与测试一行都不用改。

## B02-027 的原始意图保持

「source.kind 单一真源，GUI 白名单与内核注册共用，不再手抄副本」—— 这条仍然成立：
现在两个消费者都从**同一个**元组读，只是那个元组的定义搬到了一个更轻的文件里。
"""

from __future__ import annotations

#: 通用采集器的 kind（由 `sources.register` 注册）。
GENERIC_SOURCE_KINDS: tuple[str, ...] = (
    "static_html", "crawl", "focused", "incremental", "url_list", "rest",
    "graphql", "form", "sitemap", "feed", "browser", "file", "media",
    "websocket", "sse", "long_poll", "redis", "scrapy",
)

#: 内置站点适配器的 kind（由 `site_adapters` 注册专用类；重名冲突）。
SITE_ADAPTER_KINDS: tuple[str, ...] = (
    "site_wordpress", "site_drupal", "site_mediawiki", "site_discourse",
)

#: 校验用的**全量白名单**（GUI 白名单与内核注册共用这一份）。
SUPPORTED_SOURCE_KINDS: tuple[str, ...] = GENERIC_SOURCE_KINDS + SITE_ADAPTER_KINDS
