"""自然语言需求 → 任务的这几条口径，是 2026-11-26 用**真实外网站点**测出来的。

## 为什么单独立一个文件

既有的 `test_request_semantics_r41.py` 用的是 `example.com` / `books.toscrape.com`
这类**只解析不触网**的样例，覆盖的是「语义有没有被承载」。本文件覆盖的是另一件事：
**同一句需求交给不同的消费方，得到的结论一致吗**，以及**生成出来的配置在真跑时
会不会静默少给数据**。

两条都是端到端实测暴露的，单测层面本来"全绿"：

| 缺陷 | 单测能看到吗 | 端到端看到的是 |
|---|---|---|
| `JSONL` 解析成 `json` | 看不到（没人测过这个格式） | GUI 口径下三种输出**全 false** |
| 「下载」被当成「下载附件」 | 看不到 | 确认单写着「需求包含 PDF」，用户没说过 |
| `follow_xpath` + 默认 `max_depth=3` | 看不到 | 交付 4 页 80 条，实际站点有 1000 本 |

第三条最狠：**配置看着是对的，跑起来也不报错，只是数据少了一个数量级。**
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from omnicrawler.core.config import load_config, validate_config
from omnicrawler.core.config_defaults import DEFAULTS
from omnicrawler.extraction.intelligent_scraper import align_requested_fields, analyze_to_config
from omnicrawler.services import natural_language_task as nlt
from omnicrawler.services.natural_language_task import compile_natural_language

# ── ① 输出格式：jsonl / json 必须收敛到同一个合法键 ──────────────────────


@pytest.mark.parametrize(
    "phrase",
    ["存成 JSONL", "输出 jsonl", "导出 JSON", "保存为 jsonlines", "写进 ndjson"],
)
def test_json_family_resolves_to_the_jsonl_key(phrase: str) -> None:
    """本项目只写 JSON Lines，**没有**独立的 `outputs.json` 键。

    缺陷原文：规则表里是 `("json", r"\bjson\b|\bjsonl\b")`，于是「存成 JSONL」
    解析成 `("json",)`。`json` 不是合法 outputs 键：

    * `auto-analyze` 有一条 `"jsonl" if item == "json" else item` 的临时补丁，侥幸正确；
    * GUI 序列化是 `name in output_formats`，三种输出**全部判 false** ⇒ 不产出任何文件。

    同一个词在两处得到相反结论 —— 这就是「两份口径」。
    """
    assert nlt._extract_output_formats(phrase) == ("jsonl",)


def test_jsonl_is_a_legal_outputs_key() -> None:
    """守住上面那条的依据：jsonl 在 DEFAULTS 里，json 不在。"""
    assert "jsonl" in DEFAULTS["outputs"]
    assert "json" not in DEFAULTS["outputs"]


def test_every_requested_format_is_a_legal_outputs_key() -> None:
    """★ 这是防复发的总闸：解析层吐出的每个格式名，都必须是合法的 outputs 键。"""
    probes = ["输出 CSV", "导出 Excel", "存成 JSONL", "导出 JSON"]
    for phrase in probes:
        for name in nlt._extract_output_formats(phrase):
            assert name in DEFAULTS["outputs"], f"{phrase} 解析出不存在的输出键 {name!r}"


# ── ② 「下载」不等于「下载附件」 ─────────────────────────────────────────


def test_plain_download_wording_does_not_enable_pdf_processing() -> None:
    """实测事故：说「下载 <站点> 全部书籍信息」被解析成 download_files + process_pdf。

    确认单上写着「需求包含附件或 PDF，因此启用文件下载和 PDF 处理」——
    用户根本没说过这句话，而且那个站点是纯 HTML，没有 PDF 可处理。
    """
    task = compile_natural_language("下载 https://books.toscrape.com 全部书籍信息").task
    assert task.download_files is False
    assert task.process_pdf is False


def test_explicit_attachment_wording_still_enables_it() -> None:
    """收窄不能收成"永远不启用" —— 真的说了附件就必须启用。"""
    task = compile_natural_language("采集 https://example.org/reports 的公告附件和 PDF").task
    assert task.download_files is True
    assert task.process_pdf is True


# ── ③ 宾语前置的语序也要取出字段 ─────────────────────────────────────────


@pytest.mark.parametrize(
    ("demand", "expected"),
    [
        ("抓取 https://quotes.toscrape.com 的语录和作者", ("语录", "作者")),
        ("把 https://quotes.toscrape.com 的语录和作者抓下来", ("语录", "作者")),
        ("将 https://books.toscrape.com 的书名、价格采集下来", ("书名", "价格")),
    ],
)
def test_field_list_is_found_in_both_word_orders(demand: str, expected: tuple[str, ...]) -> None:
    """「把 A、B 抓下来」和「抓取 A、B」是同义，字段清单不能只在前者生效。

    丢字段的后果不是报错，而是**静默放大范围**：`align_requested_fields` 拿到空列表
    直接返回全部自动推断字段 ⇒ 用户以为只要两列，实际拿到整张表。
    """
    assert compile_natural_language(demand).task.fields == expected


# ── ④ 同义字段名不等于「字段不存在」 ─────────────────────────────────────


def test_common_synonyms_resolve_instead_of_failing() -> None:
    """实测事故：「抓取所有书的**书名**、价格和评分」直接失败。

    站点生成的字段叫「标题」，而需求里写「书名」。报错是
    `需求字段没有可靠规则：书名` —— 这句话在说「页面上没有这个字段」，
    实际是「我们不认识这个说法」。
    """
    fields = {
        "标题": {"selector": "h3"},
        "价格": {"selector": ".price"},
        "评分": {"selector": ".star"},
    }
    selected = align_requested_fields(fields, ["书名", "价格", "评分"], [])
    assert set(selected) == {"书名", "价格", "评分"}
    assert selected["书名"]["required"] is True


def test_unknown_field_is_still_rejected() -> None:
    """扩同义词表**不能**退化成"找不到就忽略"——那会把用户要的字段静默丢掉。"""
    with pytest.raises(ValueError, match="秘密字段"):
        align_requested_fields({"作者": {"selector": ".author"}}, ["秘密字段"], [])


# ── ⑤ 分页配置必须给自己留够深度 ─────────────────────────────────────────


def _crawl_items_html(pages: int = 3) -> str:
    articles = "".join(
        f'<article class="product_pod"><h3><a href="/p/{i}" title="B{i}">B{i}</a></h3>'
        f'<p class="price_color">£{i}.00</p></article>'
        for i in range(1, 4)
    )
    # 用真实的「下一页」形状：检测器要看到 `<li class="next"><a href=…>`，
    # 普通页码链接会被判成 url_param 或干脆不判。
    del pages
    pager = '<ul class="pager"><li class="next"><a href="page-2.html">next</a></li></ul>'
    return (
        '<html><body><section class="items">' + articles + "</section>"
        + pager + "</body></html>"
    )


def test_generated_config_raises_max_depth_for_next_link_pagination() -> None:
    """★ 这条钉的是一个**会静默少给一个数量级数据**的缺陷。

    实测（books.toscrape.com，同一份配置只改一个值）：

        crawl.max_depth 默认 3   → 4 页 / 80 条     （第 5 页起被 depth_limit 拦掉）
        crawl.max_depth: 50      → 30 页 / 600 条

    原因：`source.follow_xpath` 让翻页链上每页都比上页深一层，而生成器只写了
    `follow_xpath` + `max_pages`，没写 `max_depth` ⇒ 落回默认 3。
    本项目自己的 `templates/generic/next_link_pagination.yaml` 早就写 `max_depth: 100`。

    **判据不是"配置里有 max_depth"，而是"深度不小于页数预算"** ——
    只断言字段存在的话，写 `max_depth: 3` 也能过。
    """
    config = analyze_to_config(_crawl_items_html(), "https://books.toscrape.com/")
    assert config["source"].get("follow_xpath"), "这个夹具应被判成 next-link 分页"
    crawl = config["crawl"]
    # 上界是配置校验允许的 100（`core/config.py`），所以「够不够」要按那个上限算：
    # 页数预算 200 时深度最多也只能给到 100 —— 那已经翻完 100 页了。
    # 判据刻意写成「覆盖到深度上限为止」，而不是「等于页数」：后者会逼着实现越过校验上界。
    assert crawl["max_depth"] == min(crawl["max_pages"], 100), (
        f"深度 {crawl['max_depth']} 与页数预算 {crawl['max_pages']} 不匹配 —— 分页会静默停住"
    )


def test_generated_max_depth_stays_within_the_validated_range() -> None:
    """抬高深度不许越过配置校验的 0–100 上界（否则生成的配置自己就校验不过）。"""
    config = analyze_to_config(_crawl_items_html(), "https://books.toscrape.com/")
    assert 0 <= config["crawl"]["max_depth"] <= 100

    root = Path(tempfile.mkdtemp(prefix="omnicrawler-depth-"))
    path = root / "c.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    # `validate_config` 收的是**已加载的 AppConfig**，不是路径 —— 先 load。
    assert validate_config(load_config(path)) == ([], [])
