"""内嵌 SSR 数据（__NEXT_DATA__ / __NUXT__ / __INITIAL_STATE__）的提取与自动配置。

## 为什么值得单独钉住

现代站点把整页数据放在 `<script type="application/json">` 里，DOM 只有一个空挂载点。
2026-11-27 实测：这类页面走结构分析得到 `page_type=single / patterns=0 / fields=[]`，
直接抛「未识别出列表」——而数据其实就在同一份 HTML 里，一个字节都没少。

用户当时的两个选择都不可接受：开浏览器渲染（慢一个数量级），或手写选择器
（页面上根本没有对应元素）。

## 这些用例各守什么

| 用例 | 守的是什么 |
|---|---|
| 载荷识别 | `type=application/json` 与 `window.__X__=` 两种形态都认，ld+json 不抢 |
| 函数式载荷 | Nuxt 3 的 `{...}()` 含函数调用 ⇒ **宁可不提取**，不半解析 |
| Nuxt 公共前缀 | `data[0]` 是共享值、`data[1..]` 是记录 ⇒ 合起来才是完整记录 |
| 残片门禁 | 只有 ID 的数组不算记录（否则用户拿到一列 ID 却以为拿到了列表） |
| 记录级字段 | `record_level=true` 才逐条取值；缺了它就是广播假数据 |
| 文档级字段 | 不带 `record_level` 的 embedded_json 字段**只取一次**，单独成 document 记录 |
| 优先级 | **有 DOM 列表时不许改用内嵌数据**（否则既有站点全部变样） |
| 不占用别的来源 | 内嵌数据不改变 `jsonld` / `opengraph` 的结果 |
"""

from __future__ import annotations

from pathlib import Path

from omnicrawler.core.config import AppConfig
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.extraction.embedded_json import (
    extract_embedded_payloads,
    find_embedded_records,
)
from omnicrawler.extraction.extractors import HTMLProcessor
from omnicrawler.extraction.intelligent_scraper import analyze_to_config, verify_config
from omnicrawler.extraction.item_path import _shared_prefix_records, rank_item_paths

SPA_HTML = (
    '<!DOCTYPE html><html><head><title>Shop</title>'
    '<script id="__NEXT_DATA__" type="application/json">'
    '{"props":{"pageProps":{"products":['
    '{"id":1,"name":"Alpha","price":9.9,"brand":"Acme","image":"/a.png"},'
    '{"id":2,"name":"Beta","price":19.9,"brand":"Acme","image":"/b.png"},'
    '{"id":3,"name":"Gamma","price":29.9,"brand":"Beta","image":"/c.png"}'
    ']}}}'
    '</script></head><body><div id="__next"></div></body></html>'
)


def _result(html: str, url: str = "https://example.org/shop") -> FetchResult:
    return FetchResult(
        CrawlRequest(url),
        url,
        200,
        {"content-type": "text/html; charset=utf-8"},
        html.encode("utf-8"),
        0.01,
    )


def _config(tmp_path: Path, raw: dict) -> AppConfig:
    root = tmp_path
    return AppConfig(root / "c.yaml", root, raw, root / "work")


# ── 载荷识别 ────────────────────────────────────────────────────────────


def test_application_json_script_is_recognised() -> None:
    payloads = extract_embedded_payloads(SPA_HTML)
    assert [item.source for item in payloads] == ["script#__NEXT_DATA__"]
    assert payloads[0].payload["props"]["pageProps"]["products"]


def test_inline_assignment_is_recognised() -> None:
    html = (
        "<html><body><script>"
        'window.__INITIAL_STATE__={"list":{"items":[{"k":"a"},{"k":"b"}]}};'
        "</script></body></html>"
    )
    payloads = extract_embedded_payloads(html)
    assert [item.source for item in payloads] == ["script:__INITIAL_STATE__"]
    assert payloads[0].payload["list"]["items"][0] == {"k": "a"}


def test_ld_json_is_left_to_the_jsonld_source() -> None:
    """★ `application/ld+json` 归 `jsonld` 来源 —— 两边都收会重复提取同一份数据。

    这条是**故意**的行为边界，不是遗漏：JSON-LD 是"给搜索引擎的整页描述"，
    与"页面自己的业务数据"语义不同（项目已为此单独建了 `jsonld` 来源）。
    """
    html = (
        '<html><head><script type="application/ld+json">'
        '{"@type":"Product","name":"X","offers":{"price":"9.9"}}'
        "</script></head><body></body></html>"
    )
    assert extract_embedded_payloads(html) == []


def test_function_valued_payload_is_rejected_not_half_parsed() -> None:
    """Nuxt 3 的 `window.__NUXT__=(function(a){...})(1)` 不是 JSON。

    ★ 半解析出来的半个对象比没有更糟 —— 它会让下游以为"数据拿到了"，
      却每条记录都缺字段。宁可返回空，走原来的报错路径。
    """
    html = (
        "<html><body><script>"
        "window.__NUXT__=(function(a){return {data:{products:[1,2]}}})(1);"
        "</script></body></html>"
    )
    assert extract_embedded_payloads(html) == []


# ── Nuxt 2 的共享前缀 ───────────────────────────────────────────────────


def test_nuxt_shared_prefix_list_is_merged() -> None:
    """Nuxt 2 把公共字段抽进 `data[0]`，业务字段留在 `data[1:]`。

    分开看：`data[*]` 只有 2 个 dict（外加一个 list），`data[0][*]` 只有 ID ——
    两处都"不像记录集合"，于是整页被判成"没有可用数据"。合起来才是完整记录。
    """
    value = [
        [{"id": 1, "brand": "Acme"}, {"id": 2, "brand": "Acme"}],
        {"name": "Alpha", "price": 9.9},
        {"name": "Beta", "price": 19.9},
    ]
    merged = _shared_prefix_records(value)
    assert merged == [
        {"id": 1, "brand": "Acme", "name": "Alpha", "price": 9.9},
        {"id": 2, "brand": "Acme", "name": "Beta", "price": 19.9},
    ]


def test_ordinary_nested_array_is_not_merged() -> None:
    """★ 反方向：普通嵌套数组**不许**被当成共享前缀拼成假记录。

    判据是「公共前缀里的键在记录里**并非全都有**」。若全都有，那它就是普通嵌套，
    硬拼会造出一条字段对不上的记录 —— 比漏提取更坏。
    """
    value = [[{"a": 1}], {"a": 2, "name": "x"}, {"a": 3, "name": "y"}]
    assert _shared_prefix_records(value) is None


def test_shared_prefix_only_applies_when_asked() -> None:
    """默认关闭：普通 REST 响应里两个数组是**各自独立的数据**，不能合并。"""
    payload = {"data": [[{"id": 1}], {"name": "x", "price": 1}]}
    default_paths = [c.path for c in rank_item_paths(payload)]
    enabled = rank_item_paths(payload, shared_prefix=True)
    assert default_paths and len(default_paths) == 1
    assert len(enabled) >= 1


# ── 残片门禁 ────────────────────────────────────────────────────────────


def test_id_only_array_is_not_a_record_set() -> None:
    """只有 ID 的数组是 Nuxt 的公共前缀残片，不是可交付的记录集合。"""
    html = (
        '<html><head><script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"pageProps":{"rows":[{"id":1},{"id":2},{"id":3}]}}}'
        "</script></head><body></body></html>"
    )
    assert find_embedded_records(html, "https://example.org/x") is None


def test_payload_inside_deep_nesting_is_found() -> None:
    """Next.js 把列表放在 `props.pageProps.<key>` —— 深度 2，必须够得着。"""
    records = find_embedded_records(SPA_HTML, "https://example.org/shop")
    assert records is not None
    assert records.item_path == "$.props.pageProps.products[*]"
    assert records.count == 3


# ── 自动配置与端到端提取 ────────────────────────────────────────────────


def test_analyze_generates_embedded_config_instead_of_failing() -> None:
    """★ 这条是整件事的**动机**：以前这张页面直接抛"未识别出列表"。"""
    advisories: list[str] = []
    config = analyze_to_config(SPA_HTML, "https://example.org/shop", advisories=advisories)
    assert config["extract"]["embedded_json"]["item_path"] == "$.props.pageProps.products[*]"
    fields = config["extract"]["fields"]
    assert {"名称", "价格", "品牌"} <= set(fields)
    # ★ 字段路径必须是**相对记录**的（与 JSONProcessor 同义），不能带 item_path
    assert fields["名称"]["path"] == "name"
    assert fields["名称"]["record_level"] is True
    # 静态抓取即可，不必开浏览器 —— 这是性能收益的来源
    assert config["source"]["kind"] == "static_html"
    joined = " ".join(advisories)
    assert "内嵌数据" in joined and "script#__NEXT_DATA__" in joined, advisories


def test_verify_counts_every_embedded_record() -> None:
    """验证阶段必须真的跑一遍：路径写错时**不会报错**，只会每条少一列。"""
    config = analyze_to_config(SPA_HTML, "https://example.org/shop")
    report = verify_config(config, SPA_HTML)
    assert report["mode"] == "embedded"
    assert report["items"] == 3
    assert report["records"] == 3
    assert all(count == 3 for count in report["fields"].values()), report["fields"]


def test_processor_extracts_one_record_per_embedded_item(tmp_path: Path) -> None:
    config = _config(tmp_path, analyze_to_config(SPA_HTML, "https://example.org/shop"))
    outcome = HTMLProcessor(config).process(_result(SPA_HTML))
    assert len(outcome.records) == 3
    names = [record.data["名称"] for record in outcome.records]
    assert names == ["Alpha", "Beta", "Gamma"]
    # 图片是资源地址 ⇒ 必须归一成绝对 URL（与 html 模式的既有约定一致）
    assert outcome.records[0].data["图片地址"] == "https://example.org/a.png"


def test_dom_list_wins_over_embedded_payload() -> None:
    """★ 优先级：**有 DOM 列表时不许改用内嵌数据**。

    否则一个既有 DOM 列表又有 SSR 载荷的站点会突然换成 JSON 链路 ——
    选择器配置、分页语义、字段命名全部变样。那不是优化，是破坏既有行为。
    """
    rows = "".join(
        f"<div class='item'><h3 class='title'>T{i}</h3>"
        f"<span class='price'>${i}.00</span></div>"
        for i in range(1, 6)
    )
    html = (
        "<html><head>"
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"pageProps":{"products":['
        '{"id":1,"name":"X1","price":1},{"id":2,"name":"X2","price":2},'
        '{"id":3,"name":"X3","price":3}]}}}'
        "</script></head><body><div class='list'>" + rows + "</div></body></html>"
    )
    config = analyze_to_config(html, "https://example.org/list")
    assert "embedded_json" not in config["extract"]
    assert config["extract"].get("item_selector"), config["extract"]


def test_embedded_document_field_is_not_broadcast(tmp_path: Path) -> None:
    """★ 不带 `record_level` 的 embedded_json 字段 = 整页级数据，只取一次。

    这正是 2026-11-27 修掉的广播缺陷形态：把整页的「定价」贴到 30 行上，
    每一行都拿到同一个值 —— 技术上叫广播，数据上是**假数据**。
    """
    html = (
        "<html><head>"
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"pageProps":{"site":{"currency":"CNY"}}}}'
        "</script></head><body>"
        "<div class='item'><h3 class='title'>A</h3></div>"
        "<div class='item'><h3 class='title'>B</h3></div>"
        "<div class='item'><h3 class='title'>C</h3></div>"
        "</div></body></html>"
    )
    raw = {
        "project": {"name": "t"},
        "source": {"kind": "static_html", "seeds": ["https://example.org/x"]},
        "extract": {
            "mode": "html",
            "item_selector": "div.item",
            "fields": {
                "标题": {"selector": "h3.title"},
                "币种": {"source": "embedded_json", "path": "$.props.pageProps.site.currency"},
            },
        },
    }
    outcome = HTMLProcessor(_config(tmp_path, raw)).process(_result(html))
    item_records = [r for r in outcome.records if r.record_type == "html_item"]
    doc_records = [r for r in outcome.records if r.record_type == "document"]
    assert len(item_records) == 3
    assert all("币种" not in record.data for record in item_records), "币种被广播到了每一行"
    assert len(doc_records) == 1
    assert doc_records[0].data["币种"] == "CNY"
