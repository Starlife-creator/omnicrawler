from __future__ import annotations

import pytest
import yaml

from omnicrawler.core.config import DEFAULTS, AppConfig, load_config
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.extraction.intelligent_scraper import analyze_to_config
from omnicrawler.sources.sources import GenericSource

LIST_PAGE = """<html><body>
<div class="items">
  <div class="item"><h2><a href="/p1">Apple iPhone</a></h2><span class="price">699</span></div>
  <div class="item"><h2><a href="/p2">Samsung Galaxy</a></h2><span class="price">799</span></div>
  <div class="item"><h2><a href="/p3">Xiaomi Mi</a></h2><span class="price">499</span></div>
  <div class="item"><h2><a href="/p4">Pixel Phone</a></h2><span class="price">599</span></div>
</div>
<a href="/list?page=2">下一页</a>
</body></html>"""


def test_analyze_to_config_placeholder_url_rejected() -> None:
    with pytest.raises(ValueError, match="真实页面 URL"):
        analyze_to_config("<html><body><p>x</p></body></html>", url="")
    with pytest.raises(ValueError, match="真实页面 URL"):
        analyze_to_config("<html><body><p>x</p></body></html>", url="file:///placeholder")


def test_analyze_to_config_contract_keys(tmp_path) -> None:
    config = analyze_to_config(
        LIST_PAGE, url="https://shop.example/list?page=1", project_name="auto-demo"
    )

    assert config["source"]["seeds"] == ["https://shop.example/list?page=1"]
    assert config["source"]["pagination"] == {"type": "page", "parameter": "page"}
    assert "pagination" not in config["crawl"]

    fields = config["extract"]["fields"]
    assert fields, "应推断出业务字段"
    for _name, rule in fields.items():
        assert "selector" in rule
        assert "attribute" not in rule, "契约用 attr，不使用 attribute"
        assert "desc" not in rule, "契约字段不含 desc"
        for key in rule:
            assert key in ("selector", "attr", "regex", "examples"), f"未知字段键: {key}"

    assert config["extract"].get("item_selector"), "列表容器应输出到 item_selector"


def test_analyze_to_config_passes_core_validation(tmp_path) -> None:
    config = analyze_to_config(LIST_PAGE, url="https://shop.example/list?page=1")
    path = tmp_path / "auto.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    loaded = load_config(path)  # 契约校验通过，不抛 ValueError
    assert loaded.source_kind == "browser"
    assert loaded.section("source").get("seeds")


NEXT_LINK_PAGE = """<html><body>
<div class="items">
  <div class="item"><h2><a href="/p1">Apple iPhone</a></h2><span class="price">699</span></div>
  <div class="item"><h2><a href="/p2">Samsung Galaxy</a></h2><span class="price">799</span></div>
  <div class="item"><h2><a href="/p3">Xiaomi Mi</a></h2><span class="price">499</span></div>
  <div class="item"><h2><a href="/p4">Pixel Phone</a></h2><span class="price">599</span></div>
</div>
<nav><ul class="pager"><li class="next"><a rel="next" href="/list/page/2/">Next</a></li></ul></nav>
</body></html>"""


def test_analyze_to_config_next_link_emits_no_click_action() -> None:
    """next_link 分页不得写成 browser.actions 的“点击下一页”。

    actions 对**每个**渲染页都执行：入口页点一下就跳走，第一页内容全丢
    （实测 quotes.toscrape.com/js：最终 URL 变成 /js/page/2/、正文只剩未渲染
    骨架、0 条记录；而分析期自校验不跑 actions，于是“校验通过 10 条、运行 0 条”）。
    “下一页”本就是同站 <a href>，交给 source.discover 的链接发现即可。
    """
    config = analyze_to_config(NEXT_LINK_PAGE, url="https://shop.example/list/")

    actions = (config.get("browser") or {}).get("actions") or []
    assert all(str(item.get("action")) != "click" for item in actions), actions
    assert "pagination" not in config.get("source", {})


def test_browser_source_discovers_links_and_inherits_render(tmp_path) -> None:
    """浏览器源必须参与链接发现，且子请求继承 render。

    - 不参与链接发现 ⇒ 用浏览器抓分页列表无法翻页（分析器只能退化成
      “点击下一页”动作，反而毁掉第一页）。
    - 子请求不继承 render ⇒ 同一页在“种子（渲染）”与“子链接（HTTP）”两条
      路径下产出不同字节，内容哈希不同 ⇒ 去重失效、同一页被重复采集
      （实测 scrapethissite：250 条变 500 条）。
    """
    raw = {
        "project": {"name": "src", "workspace": "work"},
        "source": {"kind": "browser", "seeds": ["https://shop.example/list/"]},
        "crawl": {**DEFAULTS["crawl"], "max_pages": 10, "max_depth": 2, "same_host": True},
        "http": {**DEFAULTS["http"], "user_agent": "test@example.com"},
    }
    source = GenericSource(AppConfig(tmp_path / "t.yaml", tmp_path, raw, tmp_path / "work"))
    parent = CrawlRequest("https://shop.example/list/", render=True)
    result = FetchResult(
        parent, "https://shop.example/list/", 200,
        {"content-type": "text/html; charset=utf-8"},
        b'<html><body><a href="/list/page/2/">Next</a></body></html>', 0.1, {},
    )

    children = source.discover(result)

    assert children, "浏览器源应发现同站链接"
    assert all(child.render is True for child in children), "子请求应继承 render"
