"""「字段全是兜底名」这条判据必须**只有一处**，且 GUI 与 CLI 共用。

## 事故形态（2026-11-27）

`内容_<tag>` 是「认不出语义」的产物。CLI 侧早就把它变成一句人话
（`结论: 未找到业务列表`），但 **GUI 侧一句提示都没有** ——
同一个坑在图形界面上更隐蔽（用户看不到任何 stderr）。

根因不是「忘了写提示」，是**判据散落**：CLI 走 `meaningful_field_names()` 读 `config`，
GUI 拿到的是 worker 组装好的 `fields` 列表，形状不同，于是各自写各自的。

## 判据

`count_meaningful_fields(names)` 收**名字列表**，两边都能用。
这里同时钉住三件事：

1. 兜底名的定义（前缀）只在一处；
2. 计数对 `None` / 非字符串名字不抛异常（上游数据不可信）；
3. `meaningful_field_names()` 与 `count_meaningful_fields()` **口径一致**
   —— 两条路径给不同答案就是"一处判据两个真源"。
"""

from __future__ import annotations

import pytest

from omnicrawler.extraction.intelligent_scraper.result_quality import (
    FALLBACK_PREFIX,
    count_meaningful_fields,
    is_fallback_field_name,
    meaningful_field_names,
)


def test_fallback_prefix_is_the_documented_one() -> None:
    assert FALLBACK_PREFIX == "内容_"


@pytest.mark.parametrize("name", ["内容_span", "内容_td_2", "内容_p"])
def test_fallback_names_are_recognised(name: str) -> None:
    assert is_fallback_field_name(name) is True


@pytest.mark.parametrize("name", ["标题", "价格", "title", "内容", ""])
def test_real_names_are_not_fallback(name: str) -> None:
    """`内容` 本身**不是**兜底名（没有下划线后缀）—— 别把正常字段误判。"""
    assert is_fallback_field_name(name) is False


def test_count_of_all_fallback_names_is_zero() -> None:
    """★ 这正是 GUI 要提示的那种页面：结构上有重复块，但一个业务字段都没认出。"""
    names = ["内容_span", "内容_span_2", "内容_span_3"]
    assert count_meaningful_fields(names) == 0


def test_count_mixes_real_and_fallback() -> None:
    assert count_meaningful_fields(["标题", "内容_p", "价格"]) == 2


def test_count_tolerates_none_and_non_string_names() -> None:
    """上游数据不可信：`None` / 数字不能抛异常。"""
    assert count_meaningful_fields([None, 42, "标题"]) == 2


def test_empty_list_counts_zero() -> None:
    assert count_meaningful_fields([]) == 0


def test_config_path_and_name_path_agree() -> None:
    """★ 两条入口必须同口径 —— 否则"一处判据两个真源"。"""
    config = {"extract": {"fields": {"标题": {}, "内容_p": {}, "价格": {}}}}
    from_config = len(meaningful_field_names(config))
    from_names = count_meaningful_fields(list(config["extract"]["fields"]))
    assert from_config == from_names == 2
