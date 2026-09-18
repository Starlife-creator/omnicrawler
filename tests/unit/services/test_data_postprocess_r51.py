"""记录级数据后处理（``services/data_postprocess.py``，走查 R5.1）测试。

覆盖四块：
- 规格解析：``--sort`` / ``--agg`` 的合法形态与被拒绝的形态（含糊、越界、格式错）。
- 数值口径：**"文本也是数"的唯一判据**（严格十进制字面量）——这是 R5.1 的核心，
  因为管道里根本不存在 Python 数值（normalizers 文本进文本出、CSV 不做类型推断）。
- 排序 / 分组聚合：口径可见、空值恒排最后、分组是划分、分组键保真、Decimal 精确、
  非数值计数与 ``None`` 的区别。
- 补救建议：必须**探测得出**（一个候选函数都修不好就如实说），且不得产出双后缀的绕路命名。

★ 每条断言都对着真实缺口：行为一旦回退，用例必须变红
（反向断言脚本：``.audit-tmp/w71/reverse_assert_r51.py``）。
"""

from __future__ import annotations

import pytest

from omnicrawler.services.data_postprocess import (
    AGG_FUNCTIONS,
    AggSpec,
    SortKey,
    aggregate_records,
    column_kind,
    conversion_recipes,
    is_numeric_readable,
    numeric_from_text_columns,
    parse_agg,
    parse_sort,
    render_numeric_advice,
    sort_records,
)

# ── 规格解析 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("spec", "column", "direction"),
    [
        ("价格", "价格", "asc"),
        ("价格:asc", "价格", "asc"),
        ("价格:desc", "价格", "desc"),
        (" 价格 : DESC ", "价格", "desc"),
    ],
)
def test_parse_sort_documented_forms(spec: str, column: str, direction: str) -> None:
    key = parse_sort(spec)
    assert (key.column, key.direction) == (column, direction)


def test_parse_sort_keeps_colon_inside_column_name() -> None:
    """列名本身含冒号时不能被切坏（这是用 rpartition + 方向白名单、而非"按冒号切两半"的意义）。"""
    key = parse_sort("a:b")
    assert (key.column, key.direction) == ("a:b", "asc")


@pytest.mark.parametrize("spec", ["", "   "])
def test_parse_sort_rejects_empty(spec: str) -> None:
    with pytest.raises(ValueError, match="--sort"):
        parse_sort(spec)


def test_parse_agg_documented_forms() -> None:
    spec = parse_agg("sum(价格):总价")
    assert (spec.func, spec.column, spec.alias) == ("sum", "价格", "总价")
    assert spec.output_column == "总价"
    assert spec.label == "sum(价格)"
    # 无别名时输出列名确定（供 --sort 引用）
    assert parse_agg("count").output_column == "count"
    assert parse_agg("count_distinct(分类)").output_column == "count_distinct_分类"


def test_parse_agg_function_name_is_case_insensitive() -> None:
    """`MAX(价格)` 与 `max(价格)` 是同一件事 —— 大小写不该变成一条"格式错误"。"""
    assert parse_agg("MAX( 价格 )").func == "max"
    assert parse_agg("Count_Distinct(分类)").func == "count_distinct"


@pytest.mark.parametrize(
    ("spec", "match"),
    [
        ("", "格式"),
        ("sum(", "无法解析"),
        ("median(价格)", "不支持"),
        ("sum", "需要一个列名"),
        ("count(价格)", "不接受列名"),
        ("sum(价格):count", "冲突"),
    ],
)
def test_parse_agg_rejects(spec: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        parse_agg(spec)


def test_aggregation_whitelist_is_closed() -> None:
    """扩白名单＝扩接口（必须同时补语义与守卫）⇒ 把它钉住，避免悄悄变宽。"""
    assert AGG_FUNCTIONS == ("count", "count_distinct", "sum", "avg", "min", "max")


# ── 数值口径：本模块唯一的"文本也是数"判据 ──────────────────────────────


@pytest.mark.parametrize("value", ["51.77", "-3", "+3", "007", ".5", "0", 3, 3.5, -0.25])
def test_numeric_readable_accepts_strict_decimal_literals(value: object) -> None:
    assert is_numeric_readable(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "1,234",  # 千分位与小数逗号歧义
        "1,234.56",
        "12,99",
        "£51.77",  # 带货币符号：必须由用户显式 --map 转换
        "51元",
        "1e5",  # 科学计数（保守拒绝，且会被如实回报而不是静默）
        "N/A",
        "",
        "   ",
        "abc",
        None,
        True,  # bool 参与求和只会误导
        False,
        float("nan"),
        float("inf"),
        [1],
        {"a": 1},
    ],
)
def test_numeric_readable_rejects_ambiguous_or_non_numeric(value: object) -> None:
    assert is_numeric_readable(value) is False


def test_column_kind_follows_actual_values() -> None:
    assert column_kind(["51.77", "53.74"]) == "numeric"  # 文本，但都是严格字面量
    assert column_kind(["£51.77", "£53.74"]) == "text"
    assert column_kind([1, "2.5"]) == "numeric"
    assert column_kind([1, "abc"]) == "text"
    assert column_kind([None, None]) == "numeric"  # 空列：无值可比，取哪种都不影响结果


def test_numeric_from_text_columns_reports_only_text_interpretation() -> None:
    assert numeric_from_text_columns([{"v": "51.77"}], ["v"]) == ("v",)
    assert numeric_from_text_columns([{"v": 51.77}], ["v"]) == ()  # 原生数值：没有"解读"这回事
    assert numeric_from_text_columns([{"v": "£51.77"}], ["v"]) == ()  # 判为文本，没被按数值用


# ── 排序 ────────────────────────────────────────────────────────────────


def test_sort_uses_numeric_order_for_numeric_text_column() -> None:
    """★ R5.1 核心：'9' 与 '10' 必须按数值排 —— 按文本排会得到"看着排好了其实错了"的结果。"""
    records = [{"价格": "9"}, {"价格": "10"}, {"价格": "100"}]
    ordered, receipt = sort_records(records, [SortKey("价格")])
    assert [row["价格"] for row in ordered] == ["9", "10", "100"]
    assert receipt.kinds == {"价格": "numeric"}


def test_sort_reports_text_kind_and_probes_a_fix() -> None:
    """判为文本 ⇒ 口径必须回执（用户看得见"这是文本序"），并给出实测能用的补救映射。"""
    records = [{"价格": "£9"}, {"价格": "£10"}]
    ordered, receipt = sort_records(records, [SortKey("价格")])
    assert [row["价格"] for row in ordered] == ["£10", "£9"]  # 文本序（'1' < '9'）
    assert receipt.kinds == {"价格": "text"}
    assert receipt.conversion_recipes == (("价格", "parse_money", "价格_parsed"),)


def test_sort_puts_nulls_last_in_both_directions() -> None:
    records = [{"v": "2"}, {"v": None}, {"v": "1"}]
    asc, _ = sort_records(records, [SortKey("v")])
    desc, _ = sort_records(records, [SortKey("v", descending=True)])
    assert [row["v"] for row in asc] == ["1", "2", None]
    assert [row["v"] for row in desc] == ["2", "1", None]


def test_sort_multiple_keys_first_is_primary() -> None:
    records = [
        {"a": "1", "b": "2"},
        {"a": "1", "b": "1"},
        {"a": "0", "b": "9"},
    ]
    ordered, _ = sort_records(records, [SortKey("a"), SortKey("b", descending=True)])
    assert [(row["a"], row["b"]) for row in ordered] == [("0", "9"), ("1", "2"), ("1", "1")]


def test_sort_without_keys_is_identity() -> None:
    records = [{"a": "1"}, {"a": "0"}]
    ordered, receipt = sort_records(records, [])
    assert ordered == records
    assert receipt.kinds == {}


def test_sort_missing_column_names_available_columns() -> None:
    with pytest.raises(ValueError, match="不存在的列"):
        sort_records([{"a": "1"}], [SortKey("b")])


def test_sort_does_not_mutate_input() -> None:
    records = [{"a": "2"}, {"a": "1"}]
    sort_records(records, [SortKey("a")])
    assert [row["a"] for row in records] == ["2", "1"]


# ── 分组聚合 ─────────────────────────────────────────────────────────────


def test_aggregate_partition_is_total() -> None:
    """分组是**划分**：各组行数之和必须等于输入行数（防静默丢行）。"""
    records = [{"g": "x", "v": "1"}, {"g": "y", "v": "2"}, {"g": "x", "v": "3"}]
    rows, stats = aggregate_records(records, ["g"], [AggSpec("count")])
    assert stats.grouped_rows == stats.rows_in == 3
    assert stats.partition_ok is True
    assert stats.groups == 2
    assert [(row["g"], row["count"]) for row in rows] == [("x", 2), ("y", 1)]


def test_group_by_without_aggregations_lists_distinct_keys() -> None:
    records = [{"g": "x"}, {"g": "x"}, {"g": "y"}]
    rows, stats = aggregate_records(records, ["g"], [])
    assert [row["g"] for row in rows] == ["x", "y"]
    assert stats.grouped_rows == 3


def test_group_key_keeps_raw_text() -> None:
    """分组键是给人看的**标签**：'007' 不能被显示成 '7'（那是信息丢失）。

    ⇒ 分组用原始值、而不像 count_distinct 那样用数值键 —— 这是两处**故意**不同的口径。
    """
    records = [{"code": "007", "v": "1"}, {"code": "7", "v": "1"}]
    rows, stats = aggregate_records(records, ["code"], [AggSpec("count")])
    assert [row["code"] for row in rows] == ["007", "7"]
    assert stats.groups == 2


def test_count_distinct_uses_numeric_identity() -> None:
    """去重只输出**个数**，没有标签可丢 ⇒ 用数值键，1.50 与 1.5 是同一个值。"""
    records = [{"v": "1.50"}, {"v": "1.5"}, {"v": "2"}]
    rows, _ = aggregate_records(records, [], [AggSpec("count_distinct", "v")])
    assert rows[0]["count_distinct_v"] == 2


def test_sum_and_avg_are_decimal_exact() -> None:
    """★ 金额聚合不得留下二进制浮点伪影（曾出现 avg = 51.870000000000005）。"""
    records = [{"v": "51.77"}, {"v": "53.74"}, {"v": "50.10"}]
    rows, stats = aggregate_records(records, [], [AggSpec("sum", "v"), AggSpec("avg", "v")])
    assert repr(rows[0]["sum_v"]) == "155.61"
    assert repr(rows[0]["avg_v"]) == "51.87"
    assert stats.non_numeric_cells == 0


def test_whole_table_aggregation_without_group_by() -> None:
    records = [{"v": "1"}, {"v": "2"}]
    rows, stats = aggregate_records(records, [], [AggSpec("count"), AggSpec("sum", "v")])
    assert rows == [{"count": 2, "sum_v": 3.0}]
    assert stats.group_by == ()
    assert stats.partition_ok is True


def test_sum_skips_unreadable_and_counts_them() -> None:
    records = [{"v": "10"}, {"v": "£20"}, {"v": "30"}]
    rows, stats = aggregate_records(records, [], [AggSpec("sum", "v")])
    assert rows[0]["sum_v"] == 40.0
    assert stats.non_numeric_cells == 1
    assert stats.non_numeric_columns == ("v",)
    assert stats.failure_samples and "'£20'" in stats.failure_samples[0]


def test_nulls_are_not_reported_as_non_numeric() -> None:
    """★ None 是**缺失**、不是类型错误：把它报成"非数值"就是喊狼来了。"""
    records = [{"v": "10"}, {"v": None}, {"v": "30"}]
    rows, stats = aggregate_records(records, [], [AggSpec("sum", "v")])
    assert rows[0]["sum_v"] == 40.0
    assert stats.non_numeric_cells == 0
    assert stats.non_numeric_columns == ()


def test_sum_with_all_values_unreadable_yields_none_and_reports() -> None:
    records = [{"v": "abc"}, {"v": "def"}]
    rows, stats = aggregate_records(records, [], [AggSpec("sum", "v")])
    assert rows[0]["sum_v"] is None
    assert stats.non_numeric_cells == 2
    assert stats.unconvertible_columns == ("v",)


def test_min_max_return_original_values_compared_by_kind() -> None:
    records = [{"v": "9"}, {"v": "10"}]
    rows, _ = aggregate_records(records, [], [AggSpec("min", "v"), AggSpec("max", "v")])
    assert rows[0]["min_v"] == "9"  # 数值比较：9 < 10（文本比较会得 "10"）
    assert rows[0]["max_v"] == "10"


@pytest.mark.parametrize(
    ("group_by", "aggregations", "match"),
    [
        (["nope"], [AggSpec("count")], "--group-by"),
        ([], [AggSpec("sum", "nope")], "--agg"),
    ],
)
def test_aggregate_rejects_missing_columns(
    group_by: list[str], aggregations: list[AggSpec], match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        aggregate_records([{"a": "1"}], group_by, aggregations)


def test_post_processed_flag_is_serialized() -> None:
    """`post_processed` 是计算属性，最容易在 to_dict 里漏掉 —— 漏了摘要就永远不生成。"""
    _, stats = aggregate_records([{"v": "1"}], [], [AggSpec("sum", "v")])
    payload = stats.to_dict()
    assert payload["post_processed"] is True
    assert payload["partition_ok"] is True
    assert payload["rows_in"] == payload["grouped_rows"] == 1
    assert payload["conversion_recipes"] == []
    # 序列化后仍是可 JSON 化的纯数据（tuple → list）
    assert isinstance(payload["group_by"], list)
    assert isinstance(payload["sorted_as"], dict)


# ── 补救建议：探测得出，且不撒谎 ────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "func"),
    [("£51.77", "parse_money"), ("1,234", "parse_money"), ("51元", "parse_money")],
)
def test_conversion_recipes_probe_functions_that_actually_work(value: str, func: str) -> None:
    recipes, unconvertible = conversion_recipes([{"v": value}], ["v"])
    assert recipes == (("v", func, "v_parsed"),)
    assert unconvertible == ()


def test_conversion_recipes_refuse_to_invent_advice() -> None:
    """一个候选函数都修不好 ⇒ 如实归入 unconvertible，**不甩一句 parse_number**。"""
    recipes, unconvertible = conversion_recipes([{"v": "abc"}], ["v"])
    assert recipes == ()
    assert unconvertible == ("v",)


def test_conversion_recipes_avoid_double_suffix() -> None:
    """★ 失败列本身已是 --map 产物时，建议必须"换个函数对源列重做"，
    而不是产出 ``价格_parsed_parsed`` 这种绕路命名的列。"""
    records = [{"价格": "£51.77", "价格_parsed": "£51.77"}]
    recipes, _ = conversion_recipes(records, ["价格_parsed"])
    assert recipes == (("价格", "parse_money", "价格_parsed"),)


def test_conversion_recipes_skip_readable_and_unknown_columns() -> None:
    assert conversion_recipes([{"v": "1"}], ["v"]) == ((), ())
    assert conversion_recipes([{"v": "abc"}], ["nope"]) == ((), ())


def test_render_numeric_advice_is_copyable_and_honest() -> None:
    text = render_numeric_advice(
        recipes=(("价格", "parse_money", "价格_parsed"),),
        unconvertible=("标题",),
        action="对 sum/avg 求和",
    )
    assert text is not None
    assert '--map "价格 = parse_money(价格)"' in text
    assert "价格_parsed" in text
    assert "标题" in text
    assert "无法自动转换" in text
    assert render_numeric_advice(recipes=(), unconvertible=(), action="按数值排序") is None


def test_render_numeric_advice_has_no_command_for_unconvertible_only() -> None:
    """修不好的列**不得**配上一条命令 —— 空头支票会让用户白跑一轮。"""
    text = render_numeric_advice(recipes=(), unconvertible=("标题",), action="对 sum/avg 求和")
    assert text is not None
    assert "--map" not in text
