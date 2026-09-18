from __future__ import annotations

import pytest
import yaml

from omnicrawler.core.config import DEFAULTS, AppConfig, load_config
from omnicrawler.core.models import CrawlRequest, FetchResult
from omnicrawler.extraction.intelligent_scraper import (
    AutoConfigUnverifiedError,
    RepeatingPattern,
    _check_verified,
    _parse_dom,
    analyze_to_config,
    detect_repeating_patterns,
    infer_fields,
    verify_config,
)
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
    # 走查 R5.2：旧断言写的是 `{type, parameter}` —— **那正是"配了等于没配"**：
    # 缺 `end` 时 `sources.py` 用 `end = start` ⇒ 只发第 1 页，而契约校验也不报错。
    # 现在必须给全 start/end/step（`end` 取自页面自身链接里的最大页码）。
    assert config["source"]["pagination"] == {
        "type": "page", "parameter": "page", "start": 1, "end": 2, "step": 1,
    }
    assert "pagination" not in config["crawl"]

    fields = config["extract"]["fields"]
    assert fields, "应推断出业务字段"
    for _name, rule in fields.items():
        assert "selector" in rule
        assert "attribute" not in rule, "契约用 attr，不使用 attribute"
        assert "desc" not in rule, "契约字段不含 desc"
        for key in rule:
            # `value_map`：走查 R3.6 新增的取值映射（值写在 class 名里的元素）
            assert key in ("selector", "attr", "regex", "examples", "value_map"), f"未知字段键: {key}"

    assert config["extract"].get("item_selector"), "列表容器应输出到 item_selector"


def test_analyze_to_config_passes_core_validation(tmp_path) -> None:
    config = analyze_to_config(LIST_PAGE, url="https://shop.example/list?page=1")
    path = tmp_path / "auto.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    loaded = load_config(path)  # 契约校验通过，不抛 ValueError
    # 走查 R3.1：静态 HTML 就足以生成配置 ⇒ 运行期不必启动浏览器。
    # 旧实现无条件写 `kind: browser`，实测让静态站点白跑浏览器（60 页 98s，
    # 另一例 600s 超时）。新判据是"分析用哪份 HTML，运行就用哪种抓取方式"。
    # ★ 列表页必须用 `crawl` 而不是 `static_html`：后者**不参与链接发现**
    #   （sources.py 的 can_crawl 不含它），实测同一站点只抓到 1 页 20 条。
    assert loaded.source_kind == "crawl"
    assert loaded.section("source").get("seeds")
    assert not config.get("browser"), "静态路径不该带 browser 段（否则等于仍然启动浏览器）"


def test_single_page_without_links_uses_static_html() -> None:
    """确实没有链接可跟的单页才用 static_html（最省的路径）。"""
    config = analyze_to_config(
        "<html><body><h1>Hello</h1><p>Just text, no list.</p></body></html>",
        url="https://shop.example/about",
    )
    assert config["source"]["kind"] == "static_html"
    assert not config.get("browser")


def test_rendered_html_still_yields_browser_source() -> None:
    """渲染后的 HTML 得到的配置必须走浏览器 —— 否则运行期会采到 0 条。"""
    config = analyze_to_config(LIST_PAGE, url="https://shop.example/list", rendered=True)
    assert config["source"]["kind"] == "browser"
    assert config["browser"] == {"engine": "playwright", "headless": True}


def test_force_browser_escape_hatch_wins_over_static() -> None:
    """--always-browser：判定失手时的显式逃生阀（维护者确认要保留）。"""
    config = analyze_to_config(
        LIST_PAGE, url="https://shop.example/list", force_browser=True
    )
    assert config["source"]["kind"] == "browser"
    assert config["browser"]["engine"] == "playwright"


def test_analyze_single_json_object_generates_verified_rest_config(tmp_path) -> None:
    body = '{"id": 7, "name": "Ada", "active": true}'

    config = analyze_to_config(body, url="https://api.example/users/7", project_name="api-demo")

    assert config["source"] == {"kind": "rest", "seeds": ["https://api.example/users/7"]}
    assert config["extract"]["item_path"] == "$"
    assert config["extract"]["fields"] == {
        "编号": {"path": "id"},
        "名称": {"path": "name"},
        "active": {"path": "active"},
    }
    report = verify_config(config, body)
    assert report == {
        "mode": "json",
        "items": 1,
        "records": 1,
        "fields": {"编号": 1, "名称": 1, "active": 1},
    }

    path = tmp_path / "api.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    assert load_config(path).source_kind == "rest"


def test_verify_json_config_executes_item_and_field_paths() -> None:
    body = '{"items": [{"id": 1}, {"id": 2}]}'
    config = {
        "extract": {
            "mode": "json",
            "item_path": "$.items[*]",
            "fields": {"编号": {"path": "id"}, "缺失": {"path": "missing"}},
        }
    }

    assert verify_config(config, body) == {
        "mode": "json",
        "items": 2,
        "records": 2,
        "fields": {"编号": 2, "缺失": 0},
    }

    config["extract"]["item_path"] = "$.missing[*]"
    assert verify_config(config, body)["items"] == 0
    with pytest.raises(AutoConfigUnverifiedError, match="只得到 0 条记录"):
        _check_verified(config, body)


def test_verify_json_config_rejects_records_with_no_extracted_fields() -> None:
    body = '[{"id": 1}, {"id": 2}]'
    config = {
        "extract": {
            "mode": "json",
            "item_path": "$[*]",
            "fields": {"名称": {"path": "missing"}},
        }
    }

    assert verify_config(config, body) == {
        "mode": "json",
        "items": 2,
        "records": 0,
        "fields": {"名称": 0},
    }
    with pytest.raises(AutoConfigUnverifiedError, match=r"字段填充情况 = 名称\(0\)"):
        _check_verified(config, body)


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


# ── 2026-09-13：链接外字段 + 「未识别出列表」明确诊断 ──────────────────────

SHOP_PAGE = """<html><body>
<ul class="products">
  <li class="product"><a href="/p/1"><img src="/i1.jpg"><h2 class="title">Alpha</h2></a><span class="price">$10.00</span></li>
  <li class="product"><a href="/p/2"><img src="/i2.jpg"><h2 class="title">Beta</h2></a><span class="price">$20.00</span></li>
  <li class="product"><a href="/p/3"><img src="/i3.jpg"><h2 class="title">Gamma</h2></a><span class="price">$30.00</span></li>
  <li class="product"><a href="/p/4"><img src="/i4.jpg"><h2 class="title">Delta</h2></a><span class="price">$40.00</span></li>
</ul>
</body></html>"""

SIDEBAR_ONLY_PAGE = (
    '<html><body><aside class="sidebar">'
    + "".join(f'<a href="/{c}">{c}</a>' for c in "abcd")
    + "</aside></body></html>"
)


def test_price_outside_link_is_inferred(tmp_path) -> None:
    """价格在 `<a>` 之外时也要采到（woocommerce `/shop/` 形态）。

    背景：`detect_repeating_patterns` 的"直接子元素含标题"加分（+0.15）会让 `<a>`
    （含 img + h2）压过外层 `<li>`（含 a + span.price），而**价格在 `<a>` 之外** ——
    旧实现无条件取 `patterns[0]`，于是字段只剩「标题 + 图片地址」，价格永远取不到。
    现在 `infer_fields` 在前几个候选之间按「能推断出多少可用字段」择优。
    """
    config = analyze_to_config(SHOP_PAGE, url="https://shop.example/products/")

    assert config["extract"]["item_selector"] == "body > ul.products > li.product", (
        "容器应落在 <li>（整张卡片）而不是卡片内的 <a>，否则卡片外字段永远取不到"
    )
    fields = config["extract"]["fields"]
    assert "价格" in fields, f"价格必须被推断出来：{list(fields)}"
    assert fields["价格"]["examples"][0] == "$10.00"
    assert fields["价格"]["selector"] == "span.price"


def test_infer_fields_prefers_richer_candidate() -> None:
    """契约级：把「字段更全」的候选故意排在后面，仍应被选中。

    这条不依赖 `detect_repeating_patterns` 的打分（打分被多处用例与实测站点标定过），
    只锁 `infer_fields` 的择优判据本身。
    """
    nodes = _parse_dom(SHOP_PAGE)
    poor = RepeatingPattern(
        css_path="body > ul.products > li.product",  # 指向卡片内的 <a>：只有 图片/标题
        count=4,
        sample_texts=[],
        child_structure=["img", "h2.title"],
        depth=4,
        score=0.9,                                   # 分数更高，但字段更少
    )
    rich = RepeatingPattern(
        css_path="body > ul.products",               # 指向整张卡片：多出「价格」
        count=4,
        sample_texts=[],
        child_structure=["a", "span.price"],
        depth=3,
        score=0.5,
    )

    names = [
        field["name"]
        for field in infer_fields([poor, rich], nodes)
        if not field.get("is_container")
    ]
    assert "价格" in names, f"应选字段更全的候选：{names}"


def test_page_without_business_list_reports_missing_list() -> None:
    """页面只在页面框架（导航/侧边栏/页脚）里有重复元素 ⇒ 明确报「未识别出列表」。

    背景：旧实现会产出一份指向 `aside.sidebar > a` 的配置并"试跑通过"（4 条记录）——
    用户拿到的是侧边栏，不是业务列表（真实场景报告 S4）。
    """
    with pytest.raises(AutoConfigUnverifiedError, match="未识别出列表"):
        analyze_to_config(SIDEBAR_ONLY_PAGE, url="https://shop.example/")


def test_small_but_real_list_is_not_rejected() -> None:
    """**反向守卫**：3 条的真实列表必须照常通过，别把门槛设成"误杀"。

    （测试用 4 条形态另见 `LIST_PAGE`；这里用 3 条压住边界。）
    """
    html = """<html><body><div class="items">
      <div class="item"><h2>名称一</h2><span class="price">100</span></div>
      <div class="item"><h2>名称二</h2><span class="price">200</span></div>
      <div class="item"><h2>名称三</h2><span class="price">300</span></div>
    </div></body></html>"""
    config = analyze_to_config(html, url="https://shop.example/list/")
    assert config["extract"]["item_selector"]
    assert "价格" in config["extract"]["fields"]


def test_heterogeneous_leaf_siblings_are_not_a_pattern() -> None:
    """**反向守卫**：h1 + span + p 这种"三种不同叶子"不构成重复模式。

    旧的 `leaf` 签名把它们并成一组（`count=3`），于是详情页被当成"列表"、容器落到
    `body > article > h1`，只抽得到标题 —— 且因为"有模式"，本该走的单页兜底反而走不到。
    """
    detail = (
        "<html><body><article><h1>商品名</h1><span class=\"price\">99</span>"
        "<p class=\"desc\">描述文本足够长以通过筛选</p></article></body></html>"
    )
    nodes = _parse_dom(detail)
    assert detect_repeating_patterns(nodes) == [], "不同标签的叶子不应被当成重复组"
