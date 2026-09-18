"""地址类字段必须归一为绝对 URL，且原值留在证据里（走查 R4.3）。

## 背景（0.13.0 端到端走查）

同一批站点里，图片地址有的是相对路径、有的是绝对路径，**取决于站点怎么写**：
`media/cache/…`（books.toscrape 首页）、`../../../../media/cache/…`（分类页）、
`/images/test-sites/pagination.svg`（webscraper.io）。用户拿到的是"半条链接"，
拿去下载或核对都要自己拼。

## 判据：**属性/键名**是信号，**字段名**不是

★ 这是本批最容易写错的地方。走查里有 `链接文本: 2015`（锚文字）——
如果按字段名里出现"链接/地址"就当归一对象，`2015` 会被拼成 `https://…/2015`。

所以信号只有两种，且**属性优先、互斥**：

1. 抽取规则里的 ``attr`` 属于资源地址属性（``href`` / ``src`` / ``poster`` …）；
2. 取值路径的**叶子名**属于地址键名（``url`` / ``image`` / ``thumbnail`` …）——
   JSON 侧用它（键名是载荷给的），meta/结构化侧用它（``property`` 是配置里写明的）。

本文件钉住四件事：

1. **只补全、不改写**：与 `canonicalize_url` 刻意不同（后者给链接发现用，会转小写、去默认端口、
   丢 fragment，且非 http(s) 返回 `None` —— 在字段取值里那等于**丢值**）；
2. **信号判定**（含"字段名不许作数"的反向断言）；
3. **证据保留原值**（`absolutized_from` + `raw_value`）；
4. **运行与重放同一口径**（归一放在取值出口，不在 processor）。
"""

from __future__ import annotations

import pathlib

import pytest

from omnicrawler.core.config import AppConfig
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.core.utils import absolutize_resource_url, canonicalize_url
from omnicrawler.extraction.extractors import (
    HTMLProcessor,
    JSONProcessor,
    _address_rule_kind,
    _apply_rule,
)
from omnicrawler.extraction.html_tools import parse_html

BASE = "https://books.toscrape.com/catalogue/category/books/travel_2/index.html"


# ── 1. 只补全、不改写 ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("media/cache/a.jpg", "https://books.toscrape.com/catalogue/category/books/travel_2/media/cache/a.jpg"),
        ("../../../../media/cache/a.jpg", "https://books.toscrape.com/media/cache/a.jpg"),
        ("/images/a.svg", "https://books.toscrape.com/images/a.svg"),
        ("//cdn.example.com/a.png", "https://cdn.example.com/a.png"),
        ("a.png", "https://books.toscrape.com/catalogue/category/books/travel_2/a.png"),
    ],
)
def test_relative_addresses_become_absolute(value: str, expected: str) -> None:
    assert absolutize_resource_url(BASE, value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "#section",           # 指向本页，不是资源地址
        "#",
        "mailto:a@b.c",       # 非 http(s) 的资源地址 —— 不该被改写
        "tel:+8610000000000",
        "data:image/png;base64,AAAA",
        "javascript:void(0)",
        "ftp://example.com/a",
    ],
)
def test_values_that_must_not_be_rewritten(value: str) -> None:
    """★ 返回 ``None`` 不是"改不了"，而是"**不该改**" —— 调用方保持原值。"""
    assert absolutize_resource_url(BASE, value) is None


def test_base_that_is_not_http_is_refused() -> None:
    assert absolutize_resource_url("file:///tmp/x.html", "a.png") is None


def test_only_completion_not_rewriting() -> None:
    """★ 绝对地址原样返回：站点怎么写就怎么留。

    这条同时说明**为什么没有复用 `canonicalize_url`**：后者会顺带转小写、去默认端口、
    丢 fragment —— 那是链接发现需要的语义，不是字段取值需要的。
    """
    absolute = "https://Example.COM:443/a/b?q=1#frag"
    assert absolutize_resource_url(BASE, absolute) == absolute

    canonical = canonicalize_url(BASE, absolute)
    assert canonical is not None
    assert canonical != absolute, "canonicalize_url 本就会改写（这正是不能用它做字段归一的理由）"
    assert "#frag" not in canonical, "canonicalize_url 会丢掉 fragment"


# ── 2. 信号判定：attr / 键名，**不是字段名** ─────────────────────────────


@pytest.mark.parametrize(
    ("rule", "key_path", "expected"),
    [
        ({"selector": "img", "attr": "src"}, "", "attr"),
        ({"selector": "a", "attr": "HREF"}, "", "attr"),
        ({"selector": "a", "attr": "poster"}, "", "attr"),
        ({"selector": "a", "attr": "title"}, "", ""),            # 有 attr 但不是地址属性 ⇒ 不归一
        ({"selector": "a"}, "image.url", "key"),                 # JSON：叶子名是地址键
        ({"selector": "a"}, "images", "key"),
        ({"selector": "a"}, "title", ""),
        ({"selector": "a"}, "", ""),
        # ★ 属性优先且互斥：写了 attr: title，就不该因为路径叫 image 去当归一
        ({"selector": "a", "attr": "title"}, "image.url", ""),
    ],
)
def test_address_signal(rule: dict, key_path: str, expected: str) -> None:
    assert _address_rule_kind(rule, key_path=key_path) == expected


def test_field_name_is_not_a_signal() -> None:
    """★ 反向：字段名叫「图片地址」但取值是**文本**（没有 attr）⇒ 不归一。

    走查里的 `链接文本: 2015` 就是这条判据要挡住的形态。
    """
    rule = {"selector": "a.year-link"}
    assert _address_rule_kind(rule, key_path="") == ""


# ── 3. HTML 端到端 ───────────────────────────────────────────────────


def _html_config(tmp_path: pathlib.Path, fields: dict) -> AppConfig:
    raw = {
        "project": {"name": "t", "workspace": str(tmp_path / "work")},
        "source": {"kind": "static_html", "seeds": [BASE]},
        "extract": {"mode": "html", "item_selector": "li.item", "fields": fields},
    }
    return AppConfig(tmp_path / "c.yaml", tmp_path, raw, tmp_path / "work")


def _result(body: bytes, url: str = BASE) -> FetchResult:
    return FetchResult(
        request=CrawlRequest(url=url),
        final_url=url,
        status=200,
        headers={"content-type": "text/html; charset=utf-8"},
        body=body,
        elapsed_seconds=0.0,
    )


HTML = (
    b"<html><body><ul>"
    b"<li class='item'><a href='/book/a.html'><img src='../../../../media/a.jpg'></a>"
    b"<span class='link-text'>2015</span></li>"
    b"</ul></body></html>"
)


def test_relative_src_is_absolutized_and_original_kept(tmp_path: pathlib.Path) -> None:
    config = _html_config(tmp_path, {"图片地址": {"selector": "img", "attr": "src"}})
    record = HTMLProcessor(config).process(_result(HTML)).records[0]

    assert record.data["图片地址"] == "https://books.toscrape.com/media/a.jpg"
    entry = record.evidence["图片地址"]
    assert entry["absolutized_from"] == "../../../../media/a.jpg", "原值没留在证据里"
    assert entry["raw_value"] == "../../../../media/a.jpg", "原始属性值被改写了"
    assert entry["clean_value"] == record.data["图片地址"], "证据与交付值不一致"


def test_href_is_absolutized(tmp_path: pathlib.Path) -> None:
    config = _html_config(tmp_path, {"链接地址": {"selector": "a", "attr": "href"}})
    record = HTMLProcessor(config).process(_result(HTML)).records[0]
    assert record.data["链接地址"] == "https://books.toscrape.com/book/a.html"


def test_text_field_named_like_an_address_is_not_rewritten(tmp_path: pathlib.Path) -> None:
    """★ 走查真实形态：字段名叫「链接文本」，值是 `2015` —— 不许被拼成 URL。"""
    config = _html_config(tmp_path, {"链接文本": {"selector": "span.link-text"}})
    record = HTMLProcessor(config).process(_result(HTML)).records[0]
    assert record.data["链接文本"] == "2015"
    assert "absolutized_from" not in record.evidence["链接文本"]


def test_absolute_value_is_left_alone_without_evidence(tmp_path: pathlib.Path) -> None:
    body = b"<html><body><ul><li class='item'><img src='https://cdn.example.com/a.jpg'></li></ul></body></html>"
    config = _html_config(tmp_path, {"图片地址": {"selector": "img", "attr": "src"}})
    record = HTMLProcessor(config).process(_result(body)).records[0]
    assert record.data["图片地址"] == "https://cdn.example.com/a.jpg"
    assert "absolutized_from" not in record.evidence["图片地址"], "没改动就不该有改动记录"


@pytest.mark.parametrize("value", ["#top", "mailto:a@b.c", "javascript:void(0)"])
def test_non_resource_values_are_left_alone(tmp_path: pathlib.Path, value: str) -> None:
    body = f"<html><body><ul><li class='item'><a href='{value}'>x</a></li></ul></body></html>".encode()
    config = _html_config(tmp_path, {"链接地址": {"selector": "a", "attr": "href"}})
    record = HTMLProcessor(config).process(_result(body)).records[0]
    assert record.data["链接地址"] == value


def test_all_true_list_field_is_absolutized_elementwise(tmp_path: pathlib.Path) -> None:
    body = (
        b"<html><body><ul><li class='item'>"
        b"<img src='/a.png'><img src='/b.png'><img src='https://cdn.example.com/c.png'>"
        b"</li></ul></body></html>"
    )
    config = _html_config(tmp_path, {"图片地址": {"selector": "img", "attr": "src", "all": True}})
    record = HTMLProcessor(config).process(_result(body)).records[0]
    assert record.data["图片地址"] == [
        "https://books.toscrape.com/a.png",
        "https://books.toscrape.com/b.png",
        "https://cdn.example.com/c.png",
    ]
    assert record.evidence["图片地址"]["absolutized_from"] == ["/a.png", "/b.png", "https://cdn.example.com/c.png"]


def test_xpath_rule_with_url_attr_is_absolutized(tmp_path: pathlib.Path) -> None:
    """xpath 走的是另一条取值分支 —— 一并归一，否则"看起来能用"却给半条链接。"""
    pytest.importorskip("lxml")
    config = _html_config(tmp_path, {"图片地址": {"xpath": "//img", "attr": "src"}})
    record = HTMLProcessor(config).process(_result(HTML)).records[0]
    assert record.data["图片地址"] == "https://books.toscrape.com/media/a.jpg"
    assert record.evidence["图片地址"]["absolutized_from"] == "../../../../media/a.jpg"


# ── 4. JSON 端到端（信号是路径叶子名） ─────────────────────────────────


def _json_config(tmp_path: pathlib.Path, fields: dict) -> AppConfig:
    raw = {
        "project": {"name": "t", "workspace": str(tmp_path / "work")},
        "source": {"kind": "rest", "seeds": ["https://api.example.org/v1/items"]},
        "extract": {"mode": "json", "item_path": "$.items[*]", "fields": fields},
    }
    return AppConfig(tmp_path / "c.yaml", tmp_path, raw, tmp_path / "work")


JSON_BODY = b'{"items": [{"image": "/img/1.png", "name": "A", "homepage": "https://a.example.org"}]}'


def test_json_address_key_is_absolutized(tmp_path: pathlib.Path) -> None:
    config = _json_config(tmp_path, {"图片地址": {"path": "image"}, "名称": {"path": "name"}})
    record = JSONProcessor(config).process(_result(JSON_BODY, "https://api.example.org/v1/items")).records[0]
    assert record.data["图片地址"] == "https://api.example.org/img/1.png"
    assert record.evidence["图片地址"]["absolutized_from"] == "/img/1.png"
    assert record.data["名称"] == "A", "非地址字段被误改"


def test_json_non_address_key_is_left_alone(tmp_path: pathlib.Path) -> None:
    config = _json_config(tmp_path, {"标题": {"path": "name"}})
    record = JSONProcessor(config).process(_result(JSON_BODY, "https://api.example.org/v1/items")).records[0]
    assert record.data["标题"] == "A"


# ── 5. 运行与重放同一口径 ─────────────────────────────────────────────


def test_the_shared_outlet_normalizes_when_called_directly(tmp_path: pathlib.Path) -> None:
    """★ 归一放在**取值出口**（`_apply_rule`），而不是 `HTMLProcessor` —— 重放直接调它。"""
    document = parse_html(HTML)
    value, trace = _apply_rule(
        document, {"selector": "img", "attr": "src"}, base_url=BASE
    )
    assert value == "https://books.toscrape.com/media/a.jpg"
    assert trace["absolutized_from"] == "../../../../media/a.jpg"


def test_no_base_url_means_no_normalization(tmp_path: pathlib.Path) -> None:
    """没有 base 就不猜（插件、单测等调用方不传 base 时行为不变）。"""
    document = parse_html(HTML)
    value, trace = _apply_rule(document, {"selector": "img", "attr": "src"})
    assert value == "../../../../media/a.jpg"
    assert "absolutized_from" not in trace


def test_replay_passes_the_same_base_url() -> None:
    """重放必须拿到同一个 base —— 否则"重放值"与"运行值"不一致（同一件事两处口径）。"""
    import omnicrawler.services.replay as replay

    assert 'base_url=params.get("base_url")' in replay._REPLAY_SCRIPT
    source = pathlib.Path(replay.__file__).read_text(encoding="utf-8")
    assert '"base_url": url or ""' in source, "重放子进程没拿到 base_url，归一会在重放时消失"
