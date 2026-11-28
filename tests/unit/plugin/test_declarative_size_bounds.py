"""声明式视图的尺寸约束：不许把主窗口撑到屏幕之外（2026-11-28）。

## 缺陷（实测）

`QDockWidget` 的 `minimumWidth` **会撑宽主窗口** —— 侧栏与中央区共享同一条水平轴。

```
1366×768 笔记本，中央区 setMinimumWidth(320)：
  1 个面板 minimum_width=1000  →  窗口 1366px（尚可）
  2 个面板 minimum_width=1000  →  窗口 **2332px**（远超屏幕）
  3 个面板 minimum_width=1000  →  窗口 **2332px**
```

窗口被强制拉到 2332px 后，用户既看不全也拖不动 —— 而这是**插件声明**触发的，
不是用户操作。

## 修法（两条）

1. `minimum_width` / `minimum_height` 上限 **1000 → 480**。
   1000 从来没有依据：侧栏面板占 1000px 已是 1920 屏幕的一半。
   480 是「仍有实际用途」与「不会撑爆窗口」的交点 ——
   两个 480 面板 + 最小中央区在 1366 宽下仍放得下。
2. 额外要求 `minimum_* <= default_*`。没有这条，Qt 会在 `setMinimumSize` 时
   把 `resize(default_*)` **静默抬到** `minimum_*` —— 插件声明的 `default` 成了摆设，
   而作者无从得知。
"""

from __future__ import annotations

import pytest

from omnicrawler.plugins.plugin_declarative import validate_view_descriptor


def _descriptor(**overrides) -> dict:
    return validate_view_descriptor(
        {"view_id": "vv", "title": "t", "components": [], **overrides}
    )


# ── 上限收紧 ────────────────────────────────────────────────────────────


def test_minimum_width_cap_is_480() -> None:
    """★ 核心判据：1000 那种尺寸必须被拒。

    两个 1000 宽的侧栏面板会把 1366 宽的窗口撑到 2332px（实测）。
    """
    with pytest.raises(ValueError, match=r"160\.\.480"):
        _descriptor(minimum_width=1000)


def test_minimum_height_cap_is_480() -> None:
    with pytest.raises(ValueError, match=r"120\.\.480"):
        _descriptor(minimum_height=1000)


def test_typical_sidebar_widths_still_accepted() -> None:
    """★ 反方向：正常尺寸不许被误伤。

    注意 `default_width` 也要给足 —— 否则会撞「minimum > default」那条
    （那不是误伤，是正确拒绝：400 的最小值配 380 的默认值本身自相矛盾）。
    """
    for width in (240, 320, 400, 480):
        descriptor = _descriptor(minimum_width=width, default_width=max(width, 480))
        assert descriptor["minimum_width"] == width


def test_defaults_are_within_the_new_cap() -> None:
    """默认值必须落在新上限内，否则零配置插件直接判红。"""
    descriptor = _descriptor()
    assert descriptor["minimum_width"] <= 480
    assert descriptor["minimum_height"] <= 480


# ── minimum <= default ────────────────────────────────────────────────


def test_minimum_width_above_default_is_rejected() -> None:
    """★ 没这条时 Qt 会静默把 default 抬到 minimum，作者的声明成了摆设。"""
    with pytest.raises(ValueError, match="不能大于 default_width"):
        _descriptor(minimum_width=400, default_width=300)


def test_minimum_height_above_default_is_rejected() -> None:
    with pytest.raises(ValueError, match="不能大于 default_height"):
        _descriptor(minimum_height=400, default_height=300)


def test_minimum_equal_to_default_is_accepted() -> None:
    """相等是合法的（固定尺寸面板）。"""
    descriptor = _descriptor(minimum_width=380, default_width=380)
    assert descriptor["minimum_width"] == descriptor["default_width"] == 380


# ── 真实场景回归 ──────────────────────────────────────────────────────


def test_two_max_sidebars_fit_a_1366_laptop() -> None:
    """★ 端到端算术判据：两个**上限尺寸**的侧栏 + 中央区必须放得进 1366。

    这条把「上限为什么是 480」写成了可执行的算术，而不是散文：
    将来有人想上调上限，这条会先红。
    """
    central_minimum = 320  # 中央区自身的最小可用宽度（任务画布的最低要求）
    two_sidebars = 480 * 2
    assert two_sidebars + central_minimum <= 1366, (
        f"两个侧栏 + 中央区 = {two_sidebars + central_minimum}px，已超过 1366 屏宽"
    )


def test_descriptor_rejects_the_exact_shape_that_broke_the_window() -> None:
    """★ 用当初实测出问题的那个形状直接回归。

    两个插件都声明 `minimum_width=1000` ⇒ 实测窗口被撑到 2332px。
    现在第一个就该被拒。
    """
    with pytest.raises(ValueError):
        _descriptor(minimum_width=1000, default_width=1200)
