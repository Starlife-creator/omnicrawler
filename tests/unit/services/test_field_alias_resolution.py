"""字段对齐：**同义说法不等于字段不存在**，但也不能退化成"找不到就忽略"。

## 由来（全部是 2026-11-26 真实站点实测）

| 站点实际字段 | 用户需求 | 修之前 |
|---|---|---|
| `{标题, 首都, 人口, 面积}` | 国家名称 | `需求字段没有可靠规则：国家名称` |
| `{名称, 链接地址}` | 宝可梦名称、详情链接 | 同上 |
| `{编号, 邮箱, first_name, last_name, avatar}` | 用户姓名、邮箱 | 同上 |

三条的共同点：**站点没问题、需求也没问题，是"同一个东西的两种叫法"没被认出来。**
而报错文案说的是「页面上没有这个字段」—— 错误地暗示了用户的数据有问题。

## 不能怎么改

**不能**改成「找不到就忽略」：那会把用户要的字段静默丢掉。既有契约
`test_missing_goal_fields_are_rejected_before_success` 守着这一点，本文件也再钉一遍。
"""

from __future__ import annotations

import pytest

from omnicrawler.extraction.intelligent_scraper import align_requested_fields


def _fields(*names: str) -> dict[str, dict[str, object]]:
    return {name: {"selector": f".{name}"} for name in names}


# ── ① 站点专名前缀 + 稳定后缀 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    ("available", "requested"),
    [
        (("名称", "链接地址"), ["宝可梦名称", "详情链接"]),
        (("标题", "首都", "人口", "面积"), ["国家名称", "首都", "人口", "面积"]),
        (("姓名", "name", "email"), ["用户姓名"]),
    ],
)
def test_site_specific_prefix_still_resolves(
    available: tuple[str, ...], requested: list[str]
) -> None:
    """「**宝可梦**名称」「**用户**姓名」——前缀是站点专名，千变万化。

    枚举同义词是治不完的。稳定的是**后缀**（名称 / 姓名 / 链接 / 价格 …），
    所以表里查不到时按后缀再试一次。
    """
    selected = align_requested_fields(_fields(*available), requested, [])
    assert set(selected) == set(requested)
    for name in requested:
        assert selected[name]["required"] is True


def test_english_json_keys_resolve_for_chinese_demands() -> None:
    """JSON 载荷的字段名常是英文原键，而需求写中文。"""
    selected = align_requested_fields(
        _fields("编号", "邮箱", "first_name", "last_name", "avatar"), ["用户姓名", "邮箱"], []
    )
    assert set(selected) == {"用户姓名", "邮箱"}


# ── ② 真的没有的字段，必须报错（不能静默丢） ─────────────────────────────


def test_genuinely_missing_field_is_still_rejected() -> None:
    """扩同义词与后缀推断**不能**让"找不到"变成"跳过"。"""
    with pytest.raises(ValueError, match="秘密字段"):
        align_requested_fields(_fields("作者"), ["秘密字段"], [])


def test_error_message_lists_what_the_page_actually_has() -> None:
    """★ 错到哪一步要知道：只说「没有可靠规则」，用户不知道该改哪个字。

    把**页面上实际能取到的字段**列出来，才是能让人下一步就改对的信息。
    """
    with pytest.raises(ValueError) as excinfo:
        align_requested_fields(_fields("标题", "价格"), ["库存状态"], [])
    message = str(excinfo.value)
    assert "库存状态" in message, "要点名是哪个字段出的问题"
    assert "标题" in message and "价格" in message, f"要列出页面上有什么，实际：{message}"


# ── ③ 后缀推断不能把不相干的东西认错 ─────────────────────────────────────


def test_suffix_inference_does_not_invent_a_match() -> None:
    """后缀表只认**登记过的**后缀；对不上就仍然算缺字段。"""
    with pytest.raises(ValueError, match="库存数量"):
        align_requested_fields(_fields("标题"), ["库存数量"], [])
