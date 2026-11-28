"""W6-B：描述符里的 `foreground` 声明（浮层初始状态）。

## 为什么放在描述符里、而不是只靠 `surface.foreground.*` 能力

浮层与面板是**同一个视图**的两部分（面板里的提示条 / 角标）。分两次描述会让
「这个浮层属于哪个视图」不可推断，卸载时也容易漏掉一半。

| 用途 | 通道 |
|---|---|
| **初始声明**（随视图一起装载）| 描述符的 `foreground` 段 |
| **运行期改动**（处理进度等）| `surface.foreground.*` 能力 |

两套词汇**故意不同**：描述符是静态声明，字段更窄（`z`/`scope`/`color`/
`opacity`/`render_handle`）；能力有完整词汇（`set`/`configure`/`clear`/`capabilities`）。

## 词表的唯一真源

`FOREGROUND_Z_LEVELS` / `FOREGROUND_SCOPES` / `FOREGROUND_CONTENT_KINDS` 定义在
`plugins/plugin_declarative.py`（**纯数据、无 Qt**），`gui/foreground_surface.py`
只做 re-export。此前它们只在 GUI 模块里，于是**契约校验层无法引用** —— 想校验
「插件声明的 z 档位合法吗」就只能在 GUI 侧做，或抄一份。词表是协议事实，不是渲染细节。
"""

from __future__ import annotations

import pytest

from omnicrawler.plugins.plugin_declarative import (
    FOREGROUND_CONTENT_KINDS,
    FOREGROUND_SCOPES,
    FOREGROUND_Z_LEVELS,
    validate_view_descriptor,
)


def _descriptor(**overrides) -> dict:
    return validate_view_descriptor(
        {"view_id": "vv", "title": "t", "components": [], **overrides}
    )


# ── 词表位置与内容 ────────────────────────────────────────────────────


def test_z_levels_are_exactly_three() -> None:
    """★ 只有三档。任意整数会让「谁在上」变成插件之间的暗战。"""
    assert FOREGROUND_Z_LEVELS == ("underlay", "overlay", "modal")


def test_vocabulary_importable_without_qt() -> None:
    """★ 用**子进程**验证：只 import 词表时不许拉起 PySide6。

    测试进程里早就 import 过 Qt，直接查 `sys.modules` 永远为真 —— 那是假绿。
    """
    import subprocess
    import sys

    code = (
        "import sys; "
        "from omnicrawler.plugins.plugin_declarative import FOREGROUND_Z_LEVELS; "
        "print('PySide6' in sys.modules)"
    )
    from tools.child_env import utf8_env

    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=utf8_env(),
    )
    assert result.returncode == 0, result.stderr[-300:]
    assert result.stdout.strip() == "False", "词表模块把 PySide6 拖进来了"


def test_scopes_match_background_vocabulary() -> None:
    """前景与背景用同一套 scope 词汇（否则插件要记两套）。"""
    assert FOREGROUND_SCOPES == ("application", "workspace", "canvas")


def test_content_kinds_are_png_or_solid() -> None:
    """只接受渲染帧或纯色 —— 不接受用户媒体文件（浮层盖在内容上方）。"""
    assert FOREGROUND_CONTENT_KINDS == ("rendered", "solid")


def test_gui_module_reexports_the_same_objects() -> None:
    """★ 反方向：GUI 侧必须是 **re-export**，不是第二份字面量。"""
    pytest.importorskip("PySide6")
    from omnicrawler.gui import foreground_surface as gui_module

    assert gui_module.FOREGROUND_Z_LEVELS == FOREGROUND_Z_LEVELS
    assert gui_module.FOREGROUND_SCOPES == FOREGROUND_SCOPES
    assert gui_module.FOREGROUND_CONTENT_KINDS == FOREGROUND_CONTENT_KINDS

# ── 描述符校验 ────────────────────────────────────────────────────────


def test_absent_foreground_is_omitted_not_none() -> None:
    """★ 未声明 ⇒ **不带该键**。带 None 会让消费方多一种状态要处理。"""
    assert "foreground" not in _descriptor()


def test_valid_solid_foreground_is_normalised() -> None:
    descriptor = _descriptor(
        foreground={"z": "modal", "scope": "canvas", "color": "#112233", "opacity": 40}
    )
    assert descriptor["foreground"] == {
        "z": "modal", "scope": "canvas", "color": "#112233", "opacity": 40,
    }


def test_z_is_case_insensitive_but_normalised() -> None:
    value = _descriptor(foreground={"z": "OVERLAY", "color": "#fff"})["foreground"]
    assert value["z"] == "overlay"


def test_unknown_z_level_is_rejected() -> None:
    """★ 任意 z 值必须被拒（三档之外的整数/字符串会破坏层级可推断性）。"""
    with pytest.raises(ValueError, match="underlay/overlay/modal"):
        _descriptor(foreground={"z": "top", "color": "#fff"})


def test_unknown_scope_is_rejected() -> None:
    with pytest.raises(ValueError, match="application/workspace/canvas"):
        _descriptor(foreground={"scope": "screen", "color": "#fff"})


def test_unknown_field_is_rejected() -> None:
    """★ 白名单：hotspots 这种（已否决）字段不许出现。"""
    with pytest.raises(ValueError, match="未知字段"):
        _descriptor(foreground={"color": "#fff", "hotspots": []})


def test_empty_foreground_is_rejected() -> None:
    with pytest.raises(ValueError, match="不能是空对象"):
        _descriptor(foreground={})


def test_foreground_without_content_is_rejected() -> None:
    """只给 z 不给内容 ⇒ 浮层没东西可画，应判错而不是静默画个空层。"""
    with pytest.raises(ValueError, match="必须给出 render_handle 或 color"):
        _descriptor(foreground={"z": "overlay"})


def test_both_sources_are_rejected() -> None:
    """★ 同时给 render_handle 与 color 是自相矛盾的（哪个是内容？）。"""
    with pytest.raises(ValueError, match="不能同时给"):
        _descriptor(foreground={"color": "#fff", "render_handle": "h"})


def test_html_in_color_is_rejected() -> None:
    """★ 不接受 HTML/QSS：浮层颜色只是颜色，不是样式注入通道。"""
    for bad in ("<b>x</b>", "background: red; position: fixed"):
        with pytest.raises(ValueError, match="不是颜色"):
            _descriptor(foreground={"color": bad})


def test_opacity_is_bounded() -> None:
    assert _descriptor(foreground={"color": "#fff", "opacity": 0})["foreground"]["opacity"] == 0
    assert _descriptor(foreground={"color": "#fff", "opacity": 100})["foreground"]["opacity"] == 100
    with pytest.raises(ValueError, match="foreground.opacity"):
        _descriptor(foreground={"color": "#fff", "opacity": 101})
