"""文档级字段不许广播到每一行（2026-11-27）。

## 问题

`jsonld` / `opengraph` / `twitter` / `meta` / `browser_response` 取的是
**整页级**信息，但此前被当作普通字段塞进列表行。实测（thunderbit.com）：

```
标题= Zillow Scraper       产品定价= "Free"
标题= Pinterest Scraper    产品定价= "Free"
标题= Wikipedia Scraper    产品定价= "Free"
```

30 行都拿到同一个 `"Free"` —— 那不是「这条工具免费」，是「整页那个产品免费」。
技术上叫广播，数据上是**假数据**，而且从输出里完全看不出来。

## 修法

有列表容器时，把文档级字段**摘出来单独成一条 `document` 记录**。
导出仍是单张表，用 `record_type` 区分，并给出一条明确告警。

## 为什么选「单表 + 告警」而不是拆成两个文件

* 下游（Excel / 数据库 / 脚本）习惯一次读一个表，拆文件要改消费方；
* `record_type` 本来就是独立列，按它过滤是一行 pandas/SQL 的事；
* 拆文件的代价是「用户不知道还有个隐藏文件」，而告警是可读的。
"""

from __future__ import annotations

import pytest

from omnicrawler.extraction.extractors import is_document_level_rule


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        # 五种文档级来源 —— 全部描述整页
        ({"source": "jsonld", "path": "headline"}, True),
        ({"source": "opengraph", "property": "description"}, True),
        ({"source": "twitter", "property": "title"}, True),
        ({"source": "meta", "property": "author"}, True),
        ({"source": "browser_response", "path": "items.0"}, True),
        # 只给 jsonld_path 也算（与 _structured_rule 的入口判断对齐）
        ({"jsonld_path": "offers[*].name"}, True),
        # 普通选择器规则 —— 不是文档级
        ({"selector": ".title"}, False),
        ({"xpath": ".//h1"}, False),
        ({"selectors": [".a", ".b"]}, False),
        # 非法输入不炸
        ("not-a-dict", False),
        ({}, False),
    ],
)
def test_document_level_detection(rule, expected) -> None:
    assert is_document_level_rule(rule) is expected


def test_detection_matches_what_the_extractor_actually_treats_as_structured() -> None:
    """★ 判据必须与 `_structured_rule` 的入口**完全一致**。

    两处不一致正是这个 bug 的成因：取值走 structured（文档级），
    但「要不要广播」的判断走的是 item（列表级）。

    这里断言的是「凡是被判为文档级的，`_structured_rule` 必须真的接住」——
    用行为验证，不用源码文本（源码断言会被注释干扰，本会话踩过）。
    """
    from omnicrawler.core.models import CrawlRequest, FetchResult
    from omnicrawler.extraction.extractors import _structured_metadata, _structured_rule
    from omnicrawler.extraction.html_tools import parse_html

    html = """<html><head>
    <meta property="og:description" content="整页描述">
    <script type="application/ld+json">{"@type":"Product","name":"整页产品名"}</script>
    </head><body><div class="item">列表项</div></body></html>"""
    req = CrawlRequest("https://example.test/p")
    result = FetchResult(req, req.url, 200, {}, html.encode(), 0.0)
    structured = _structured_metadata(parse_html(html), result)

    for rule in (
        {"source": "jsonld", "path": "name"},
        {"source": "opengraph", "property": "description"},
    ):
        assert is_document_level_rule(rule)
        handled, value, _trace = _structured_rule("f", rule, structured)
        assert handled, f"被判为文档级却没人接住：{rule}"
        assert value, f"文档级规则取到空值：{rule}"
