"""「这站能不能抓」的三个新判据（2026-11-26）。

## 三条改动各自的由来

| 改动 | 由来 |
|---|---|
| 静态结果质量判据 | randomuser.me 静态出 42 条但字段只有 `内容_span` |
| 字段名相似度回退 | 说「人口数量」而页面叫「人口」，整条失败 |
| 记录相似度替换并说出来 | 静默改用户的话等于撒谎 |

★ 第三条是纪律问题，不是能力问题：用户说了「卡片标题」，我们抓「正文」并**不说**，
那是把用户的信任换成一次看起来成功的输出。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from omnicrawler.extraction.intelligent_scraper import align_requested_fields, analyze_to_config
from omnicrawler.extraction.intelligent_scraper.field_inference import suggest_closest_field
from omnicrawler.extraction.intelligent_scraper.result_quality import (
    is_weak,
    meaningful_field_names,
    quality_rank,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "real_sites"


# ── ① 静态结果质量：兜底字段名不算「有结果」 ────────────────────────────


def test_fallback_field_names_are_not_meaningful() -> None:
    """`内容_span` / `内容_p` 是「认不出语义」的产物，不是业务字段。"""
    config = {"extract": {"fields": {"内容_span": {}, "内容_p": {}}}}
    assert meaningful_field_names(config) == []


def test_meaningful_names_are_kept() -> None:
    config = {"extract": {"fields": {"标题": {}, "价格": {}, "内容_p": {}}}}
    assert meaningful_field_names(config) == ["标题", "价格"]


def test_quality_rank_prefers_structure_over_row_count() -> None:
    """★ 这是本轮最关键的排序决定。

    实测 randomuser.me：静态 42 条、字段全是 `内容_span`；渲染后可能只有几条，
    但字段是有意义的。**按条数比，渲染结果永远赢不了** —— 而静态那份是纯噪声。
    """
    noisy = ({"extract": {"fields": {"内容_span": {}}}}, {"records": 42, "fields": {"内容_span": 42}})
    clean = ({"extract": {"fields": {"标题": {}, "价格": {}}}}, {"records": 5, "fields": {"标题": 5, "价格": 5}})
    assert quality_rank(*clean) > quality_rank(*noisy), "有意义字段优先于记录数"


def test_weak_when_no_meaningful_field_at_all() -> None:
    """★ 这条同时钉住「质量排名要看有意义字段」，不是只看记录数。

    只断言 `is_weak(...) is True` 是**假守卫**：`is_weak` 里另有一句
    `meaningful == 0`，把 __init__ 里那条 `not _meaningful_field_names` 删掉它照样红/绿。
    反向断言当场判红，所以这里直接断言**排名本身**：噪声配置的排名必须低于干净配置。
    """
    noisy = {"extract": {"fields": {"内容_span": {}}}}
    clean = {"extract": {"fields": {"标题": {}, "价格": {}}}}
    assert quality_rank(noisy, {"records": 42, "fields": {"内容_span": 42}}) < quality_rank(
        clean, {"records": 5, "fields": {"标题": 5, "价格": 5}}
    )
    assert is_weak(noisy, {"records": 42, "fields": {"内容_span": 42}}, total_fields=1)


def test_auto_analyze_treats_fallback_only_fields_as_weak() -> None:
    """★ 光测 `is_weak` 不够 —— 决定「要不要去渲染」的是 `main()` 里那一行。

    我第一版就在 `is_weak` 上做断言，反向断言当场判**假守卫**：
    把 `main()` 里 `or not _meaningful_field_names(best_config)` 删掉，
    那个测试照样绿（它根本没走那条路径）。

    这里改成源码断言：`main()` 必须把「零个有意义字段」也当成 weak。
    源码断言不好看，但它是唯一能守住**调用点**的形态 ——
    和 win_shortcut 那个 `ntpath` 的教训是同一条。
    """
    import inspect

    from omnicrawler.extraction.intelligent_scraper import main as auto_analyze_main

    source = inspect.getsource(auto_analyze_main)
    assert "_meaningful_field_names(best_config)" in source, (
        "main() 必须把「零个有意义字段」判为 weak，否则 randomuser 那类站会被当成成功"
    )


def test_not_weak_when_fields_are_meaningful_and_complete() -> None:
    config = {"extract": {"fields": {"标题": {}, "价格": {}}}}
    assert not is_weak(
        config, {"records": 10, "fields": {"标题": 10, "价格": 10}}, total_fields=2
    )


def test_weak_when_meaningful_share_is_too_low() -> None:
    """字段名一半以上是兜底名 ⇒ 结构没认出来，值得再试一次渲染。"""
    config = {"extract": {"fields": {"标题": {}, "内容_p": {}, "内容_div": {}}}}
    assert is_weak(config, {"records": 9, "fields": {"标题": 9}}, total_fields=3)


# ── ② 字段名相似度回退 ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("demand", "available", "expected"),
    [
        ("人口数量", ["人口", "面积"], "人口"),
        ("商品标题", ["标题", "价格"], "标题"),
        ("价格 ", ["价格"], "价格"),
    ],
)
def test_closest_field_resolves_obvious_variants(
    demand: str, available: list[str], expected: str
) -> None:
    """字形上明显是同一个东西的差别，不该让整条命令失败。"""
    assert suggest_closest_field(demand, available) == expected


@pytest.mark.parametrize(
    ("demand", "available"),
    [
        # 跨语义：卡片标题 vs 正文 —— 这不是字形问题，必须留给用户决定
        ("卡片标题", ["正文"]),
        # 单字太容易碰巧像
        ("数", ["数量"]),
    ],
)
def test_closest_field_refuses_guessing(demand: str, available: list[str]) -> None:
    """★ 相似度回退必须**有底线**：配错了比报错更糟。

    「卡片标题 → 正文」这种跨语义的相似度很低，不该被自动配上；
    单字/多义候选同样不猜（项目既有纪律：歧义不猜）。
    """
    result = suggest_closest_field(demand, available)
    assert result == "", f"{demand} 不该被配成 {result}"


def test_two_equally_close_candidates_are_not_guessed() -> None:
    """两个候选**同样像** ⇒ 不猜。

    这是项目既有的「歧义不猜」纪律：`标题A` 与 `标题B` 对「标题」的相似度完全相同，
    挑哪个都是投硬币。
    """
    assert suggest_closest_field("标题", ["标题A", "标题B"]) == ""


def test_similarity_substitution_is_recorded_and_announced() -> None:
    """替换必须**写进配置**，而且调用方要能打印出来。

    静默改用户的话等于撒谎 —— 用户以为抓的是「人口数量」，实际抓的是「人口」，
    **列名却仍叫「人口数量」**（这是刻意的：用户的叫法保留，值取自对得上的那列）。
    """
    selected = align_requested_fields(
        {"人口": {"selector": ".pop"}, "面积": {"selector": ".area"}},
        ["人口数量", "面积"],
        [],
    )
    assert "人口数量" in selected
    assert selected["人口数量"].get("_matched_field") == "人口"


# ── ③ 真实夹具上三条判据的联合效果 ──────────────────────────────────────


def test_real_fixtures_still_produce_meaningful_fields() -> None:
    """改动不能把**本来好的**站点判成弱 —— 那会让所有站点都去开浏览器。"""
    for fixture, url, must_have in (
        ("books_main.html", "https://books.toscrape.com/", "价格"),
        ("quotes_main.html", "https://quotes.toscrape.com/", "作者"),
        ("scrapethissite_simple.html", "https://www.scrapethissite.com/pages/simple/", "首都"),
    ):
        html = (FIXTURES / fixture).read_text(encoding="utf-8", errors="replace")
        config = analyze_to_config(html, url)
        names = meaningful_field_names(config)
        assert must_have in names, f"{fixture} 丢了 {must_have}，实际：{names}"
