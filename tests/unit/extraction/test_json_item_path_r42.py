"""JSON 记录路径必须"像记录集合才选"，而不是"第一个数组就选"（走查 R4.2）。

## 背景（0.13.0 端到端走查，`.audit-tmp/w71/cases/c26`、`c47`、`c48`）

三个**单对象** API 响应都被选中了对象内部的**无关数组**：

| 用例 | URL | 旧实现选中 | 产出 | 本该是 |
|---|---|---|---|---|
| c26 | `pokeapi.co/api/v2/type/1` | `$.game_indices[*]` | 9 条 `{game_index}` | 「这个属性」本身 |
| c47 | `dummyjson.com/products/1` | `$.reviews[*]` | 3 条评论 | 「这个商品」本身 |
| c48 | `pokeapi.co/api/v2/pokemon/1` | `$.abilities[*]` | 2 条 `{is_hidden, slot}` | 「这只宝可梦」本身 |

三例机制相同 ⇒ 系统性缺陷；而质量报告里的 completeness 都是 **1.0** ——
**静默地给了用户错的东西**，比报错更坏。

★ 本文件最要紧的两条：

1. **判据与产物同名同源**：候选声明的 ``path`` 必须与真实求值器
   ``json_path(payload, path)`` 取到同一批 ``values`` —— 否则"打分选对了"只是巧合；
2. **不静默**：拿不准（单对象 / 候选接近）要说出来，嵌套没展开也要说出来，
   但**不能**把提醒变噪声（清楚的选择不发言）。
"""

from __future__ import annotations

import json

import pytest

from omnicrawler.extraction.api_discovery import discover_api_endpoints
from omnicrawler.extraction.extractors import json_path
from omnicrawler.extraction.intelligent_scraper import analyze_to_config
from omnicrawler.extraction.item_path import (
    ItemPathCandidate,
    choose_item_path,
    is_close_call,
    rank_item_paths,
    unrepresentable_path_keys,
)

# ── 与真实用例同形的夹具（测试必须自足，不依赖 .audit-tmp） ─────────────


def _pokeapi_type() -> dict:
    """`pokeapi /type/1` 的形状：单对象 + 若干内部数组。"""
    return {
        "damage_relations": {"double_damage_to": [{"name": "rock"}], "half_damage_from": []},
        "game_indices": [{"game_index": 0, "generation": {"name": "generation-i"}} for _ in range(9)],
        "generation": {"name": "generation-i", "url": "https://pokeapi.co/api/v2/generation/1/"},
        "id": 1,
        "move_damage_class": {"name": "physical"},
        "moves": [{"move": {"name": "pound"}, "version_group_details": []} for _ in range(200)],
        "name": "normal",
        "names": [{"language": {"name": "ja"}, "name": "ノーマル"} for _ in range(11)],
    }


def _dummyjson_product() -> dict:
    """`dummyjson /products/1` 的形状：单对象（17 个标量）+ reviews 数组。"""
    product = {
        "id": 1,
        "title": "Essence Mascara Lash Princess",
        "description": "popular makeup product",
        "category": "beauty",
        "price": 9.99,
        "discountPercentage": 10.48,
        "rating": 2.56,
        "stock": 99,
        "brand": "Essence",
        "sku": "BEA-ESS-ESS-001",
        "weight": 4,
        "warrantyInformation": "1 week warranty",
        "shippingInformation": "Ships in 3-5 business days",
        "availabilityStatus": "In Stock",
        "returnPolicy": "30 days return policy",
        "minimumOrderQuantity": 48,
        "tags": ["beauty", "mascara"],
        "dimensions": {"width": 15.14, "height": 13.08, "depth": 22.99},
        "meta": {"createdAt": "2025-04-30T09:41:02.053Z", "barcode": "5784719087687"},
        "reviews": [{"rating": 3, "comment": "Would not recommend!", "reviewerName": "E"} for _ in range(3)],
        "images": ["https://cdn.example.org/1.png"],
    }
    return product


def _pokeapi_pokemon() -> dict:
    """`pokeapi /pokemon/1` 的形状：单对象（8 个标量）+ 大量内部数组。"""
    return {
        "abilities": [{"ability": {"name": "overgrow"}, "is_hidden": False, "slot": 1} for _ in range(2)],
        "base_experience": 64,
        "forms": [{"name": "bulbasaur", "url": "https://pokeapi.co/api/v2/pokemon-form/1/"}],
        "game_indices": [{"game_index": 153, "version": {"name": "red"}} for _ in range(46)],
        "height": 7,
        "held_items": [],
        "id": 1,
        "is_default": True,
        "name": "bulbasaur",
        "order": 1,
        "species": {"name": "bulbasaur", "url": "https://pokeapi.co/api/v2/pokemon-species/1/"},
        "weight": 69,
    }


def _pokeapi_index() -> dict:
    """`pokeapi /pokemon?limit=20` 的形状：包装对象 + results 数组。"""
    return {
        "count": 1302,
        "next": "https://pokeapi.co/api/v2/pokemon?offset=20&limit=20",
        "previous": None,
        "results": [{"name": f"pokemon-{i}", "url": f"https://pokeapi.co/api/v2/pokemon/{i}/"} for i in range(20)],
    }


def _dummyjson_products() -> dict:
    """`dummyjson /products` 的形状：包装对象 + products 数组。"""
    return {
        "products": [
            {"id": i, "title": f"P{i}", "price": i * 1.5, "brand": "B", "rating": 4.0}
            for i in range(1, 31)
        ],
        "total": 194,
        "skip": 0,
        "limit": 30,
    }


# ── 1. 打分：单对象优先取 `$`，列表包装取数组 ───────────────────────────


@pytest.mark.parametrize(
    ("payload_factory", "url", "expected"),
    [
        (_pokeapi_type, "https://pokeapi.co/api/v2/type/1", "$"),
        (_dummyjson_product, "https://dummyjson.com/products/1", "$"),
        (_pokeapi_pokemon, "https://pokeapi.co/api/v2/pokemon/1", "$"),
        (_pokeapi_index, "https://pokeapi.co/api/v2/pokemon?limit=20", "$.results[*]"),
        (_dummyjson_products, "https://dummyjson.com/products", "$.products[*]"),
    ],
)
def test_single_object_vs_wrapper_pick_the_right_path(
    payload_factory, url: str, expected: str
) -> None:
    """★ 本条直接对应走查的三个失败用例 + 两个**必须不被改坏**的成功用例。"""
    chosen = choose_item_path(payload_factory(), url=url)
    assert chosen is not None
    assert chosen.path == expected


def test_bare_list_payload_uses_wildcard() -> None:
    chosen = choose_item_path([{"id": 1}, {"id": 2}], url="https://example.com/users")
    assert chosen is not None
    assert chosen.path == "$[*]"
    assert chosen.item_count == 2


def test_two_level_wrapper_is_reached() -> None:
    """`{data:{items:[...]}}` 这类两层包装也要能找到记录（走查 R4.2 的"1–2 层"）。"""
    payload = {"data": {"items": [{"id": i, "name": f"n{i}"} for i in range(5)]}, "meta": {"total": 5}}
    chosen = choose_item_path(payload, url="https://example.com/api/v1/things")
    assert chosen is not None
    assert chosen.path == "$.data.items[*]"


# ── 2. 判据与产物同名同源：候选 path 必须与真实求值器一致 ───────────────


@pytest.mark.parametrize(
    "payload_factory",
    [_pokeapi_type, _dummyjson_product, _pokeapi_pokemon, _pokeapi_index, _dummyjson_products],
)
def test_candidate_paths_match_the_real_evaluator(payload_factory) -> None:
    """★ 每个候选声明的 ``path`` 在**真实求值器**下必须取到同一批 ``values``。

    否则"打分选对了"只是巧合，换了求值器就会静默取错 —— 这条把两处钉在一起。
    """
    payload = payload_factory()
    ranked = rank_item_paths(payload, url="https://api.example.org/x/1")
    assert ranked, "一个候选都没有，下面的断言会变成空对空"
    for candidate in ranked:
        assert json_path(payload, candidate.path) == candidate.values, (
            f"候选 {candidate.path} 与 json_path 结果不一致"
        )


def test_candidates_are_sorted_by_score() -> None:
    ranked = rank_item_paths(_pokeapi_type(), url="https://pokeapi.co/api/v2/type/1")
    scores = [candidate.score for candidate in ranked]
    assert scores == sorted(scores, reverse=True)


# ── 3. 单一实现：api_discovery 产出的模板必须真的取得到记录 ─────────────


def test_api_discovery_template_path_actually_selects_records() -> None:
    """★ `api_discovery` 与自动配置**共用一处判据**；它的路径也要能被求值器用。

    旧实现给的是点号路径（`results`），实测 `json_path(payload, "results")` 返回
    `[[全部元素]]` —— 一条记录。这条守卫就是那次实测的固化。
    """
    payload = {"results": [{"id": 1, "title": "A"}, {"id": 2, "title": "B"}], "total": 2}
    responses = [{
        "url": "https://api.example.org/v1/items",
        "method": "GET",
        "status": 200,
        "content_type": "application/json",
        "json": payload,
    }]
    profile = discover_api_endpoints(responses)[0]
    assert profile.item_path == "$.results[*]"
    assert len(json_path(payload, profile.item_path)) == profile.item_count == 2
    # 反向：旧形态（点号路径）在这份载荷上只能取到 1 条 —— 证明"改名"是修缺陷而不是风格
    assert len(json_path(payload, "results")) == 1


# ── 4. 嵌套铺平：不再整列丢掉，也不再按字典序静默砍列 ───────────────────


def _fields_of(sample: dict) -> dict:
    config = analyze_to_config(json.dumps(sample), "https://example.com/x/1")
    return config["extract"]["fields"]


def test_nested_object_is_flattened_not_dropped() -> None:
    """旧实现 `if isinstance(value, (dict, list)): continue` 把 company/address 整列丢掉。"""
    fields = _fields_of({"id": 1, "company": {"name": "ACME", "address": {"city": "北京"}}})
    paths = {rule["path"] for rule in fields.values()}
    assert paths == {"id", "company.name", "company.address.city"}


def test_nested_array_of_scalars_keeps_the_column() -> None:
    fields = _fields_of({"id": 1, "tags": ["a", "b"]})
    assert {rule["path"] for rule in fields.values()} == {"id", "tags"}


def test_long_records_are_not_truncated_by_key_order() -> None:
    """★ 旧实现 `list(sample.items())[:12]` 按**插入序**砍掉第 13 个键之后的字段。

    这条钉住"不再静默缺列"（列数上限另说，被省略的列必须**报出来**）。
    """
    sample = {f"k{i}": i for i in range(20)}
    fields = _fields_of(sample)
    assert len(fields) == 20, f"记录被截断了：只剩 {sorted(fields)}"


def test_object_array_inside_a_record_is_reported_not_silently_dropped() -> None:
    """记录内的对象数组不铺成列（会撑爆列数）⇒ 必须**说出来**并给出下一步。"""
    notes: list[str] = []
    config = analyze_to_config(
        json.dumps({"id": 1, "reviews": [{"rating": 5, "comment": "nice"}]}),
        "https://example.com/x/1",
        advisories=notes,
    )
    assert config["extract"]["item_path"] == "$"
    text = "".join(notes)
    assert "reviews" in text, "嵌套数组被静默丢了"
    assert "reviews[*]." in text, "没给出可取子字段的实际写法"


def test_unrepresentable_keys_are_reported() -> None:
    """键名含 `.`/`[]` 时现有 JSONPath 无法表达 ⇒ 跳过**并报出来**。"""
    sample = {"id": 1, "a.b": 2, "c[0]": 3}
    assert set(unrepresentable_path_keys(sample)) == {"a.b", "c[0]"}
    notes: list[str] = []
    analyze_to_config(json.dumps(sample), "https://example.com/x/1", advisories=notes)
    assert any("无法表达" in line for line in notes)


# ── 5. 拿不准要说出来，但别把提醒变噪声 ─────────────────────────────────


def test_single_object_choice_is_announced_with_candidates() -> None:
    notes: list[str] = []
    config = analyze_to_config(
        json.dumps(_dummyjson_product()), "https://dummyjson.com/products/1", advisories=notes
    )
    assert config["extract"]["item_path"] == "$"
    text = "".join(notes)
    assert "单对象" in text
    assert "--item-path" in text, "只说'选了单对象'却不给改选的入口"
    assert "$.reviews[*]" in text, "没把候选报出来"


def test_clear_choice_produces_no_advice_noise() -> None:
    """反向：区分度很大时不许凭空产生提醒（否则提醒会被当噪声忽略）。"""
    notes: list[str] = []
    analyze_to_config(
        json.dumps(_dummyjson_products()), "https://dummyjson.com/products", advisories=notes
    )
    assert notes == []


def test_close_call_is_detected_deterministically() -> None:
    """谓词语义：次高分 ≥ 最高分的 0.7 倍才算"接近"。

    ★ 真实的 c26（`pokeapi /type/1`，`$` 5.5 分 vs `$.moves[*]` 5.0 分）本就是一次
    接近呼叫；这条用构造值把谓词本身钉死，避免依赖某个夹具的分数漂移。
    """

    def candidate(path: str, score: float) -> ItemPathCandidate:
        return ItemPathCandidate(path, [{"id": 1}], score, ())

    assert is_close_call((candidate("$", 5.0), candidate("$.a[*]", 4.0)))
    assert not is_close_call((candidate("$", 5.0), candidate("$.a[*]", 3.0)))
    assert not is_close_call((candidate("$", 5.0),)), "只有一个候选是没得选，不是选错"
    assert not is_close_call((candidate("$", 0.0), candidate("$.a[*]", 0.0)))


def test_close_call_between_two_arrays_is_announced() -> None:
    """两个数组都很像"记录集合"时，把候选报给用户自己选 —— 而不是假装很有把握。"""
    payload = {"items": [{"id": 1, "name": "a"}], "results": [{"id": 2, "name": "b"}]}
    notes: list[str] = []
    config = analyze_to_config(json.dumps(payload), "https://example.com/x", advisories=notes)
    assert config["extract"]["item_path"] == "$.items[*]"
    text = "".join(notes)
    assert "接近" in text
    assert "$.results[*]" in text, "报了'接近'却不把次优候选说出来"


# ── 6. 显式覆盖入口（`--item-path`） ───────────────────────────────────


def test_item_path_override_wins_over_scoring() -> None:
    config = analyze_to_config(
        json.dumps(_dummyjson_product()),
        "https://dummyjson.com/products/1",
        item_path_override="$.reviews[*]",
    )
    assert config["extract"]["item_path"] == "$.reviews[*]"
    paths = {rule["path"] for rule in config["extract"]["fields"].values()}
    assert {"rating", "comment", "reviewerName"} <= paths


def test_item_path_override_that_selects_nothing_fails_loudly() -> None:
    """覆盖路径取不到记录 ⇒ 报错并给出候选，不许悄悄回退成别的配置。"""
    from omnicrawler.extraction.intelligent_scraper import AutoConfigUnverifiedError

    with pytest.raises(AutoConfigUnverifiedError) as excinfo:
        analyze_to_config(
            json.dumps(_dummyjson_product()),
            "https://dummyjson.com/products/1",
            item_path_override="$.nothing[*]",
        )
    message = str(excinfo.value)
    assert "$.nothing[*]" in message
    assert "$.reviews[*]" in message, "报错了却不告诉用户有哪些候选"


def test_item_path_override_on_non_json_page_is_reported() -> None:
    """对非 JSON 页面用 `--item-path` ⇒ 如实说"未生效"，不静默忽略。"""
    notes: list[str] = []
    analyze_to_config(
        "<html><body><p>x</p></body></html>",
        "https://example.com/x",
        item_path_override="$.a[*]",
        advisories=notes,
    )
    assert any("未生效" in line for line in notes)


def test_cli_exposes_the_override_flag() -> None:
    """入口必须真的在 CLI 上可达 —— 否则又是一处"声明了但没接"。"""
    import argparse

    from omnicrawler.cli._parsers import extraction as extraction_parsers

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    extraction_parsers.configure(subparsers)
    parsed = parser.parse_args(["auto-analyze", "page.json", "--item-path", "$.a[*]"])
    assert parsed.item_path == "$.a[*]"
    assert parsed.input == "page.json"


def test_cli_handler_forwards_the_override_flag(monkeypatch) -> None:
    """命令能解析 ≠ 参数会被传下去 —— "声明了但没接线"正是走查反复出现的失败形态。"""
    import argparse
    import sys

    from omnicrawler.cli import _handlers
    from omnicrawler.extraction import intelligent_scraper

    captured: dict[str, list[str]] = {}

    def _fake_main() -> None:
        captured["argv"] = list(sys.argv)

    monkeypatch.setattr(intelligent_scraper, "main", _fake_main)
    args = argparse.Namespace(
        input="page.json", output="", url="", always_browser=False, item_path="$.a[*]"
    )
    _handlers._run_auto_analyze(args)
    assert captured["argv"] == ["auto-analyze", "page.json", "--item-path", "$.a[*]"]
