"""图标颜色**只来自主题**：SVG 里不得写死颜色，强调点必须随主题切换（W6.7-⑤ / A-40 残留）。

## 背景

审查记录 §21.7 记着一条 A-40 残留：「图标改用常量后仍是**固定强调色**，不随主题切换」——
`monitor_active`（带告警点的监测图标）里的点写死成 `#D83B01`，深/浅主题下都是同一个橙色，
与图标其余部分（`stroke="currentColor"` → 渲染时取主题令牌）不一致。

现在 SVG 里改写成 :data:`ICON_ACCENT_PLACEHOLDER`，**渲染时**从主题令牌（`ICON_ACCENT_TOKEN`）
取色，并进入缓存键。本文件把它固定成两条断言：

1. **静态**：任何图标 SVG 都不得含硬编码 `#RRGGBB` —— 颜色只能来自 `currentColor` 或占位符；
2. **动态**：切换主题后同一个图标的渲染结果**确实改变**（`QIcon.cacheKey()` 不同），
   且强调点取到的就是该主题的令牌值。
"""

from __future__ import annotations

import importlib.util
import re

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None, reason="GUI 测试需要 PySide6"
)

_APP = None


def _app():
    global _APP
    if _APP is None:
        from PySide6.QtWidgets import QApplication

        _APP = QApplication.instance() or QApplication([])
    return _APP


def test_no_icon_svg_hardcodes_a_color() -> None:
    """图标 SVG 里出现硬编码颜色就是"不随主题"的种子——一律判红。"""
    from omnicrawler.gui.icon_registry import _SVG_ICONS

    assert _SVG_ICONS, "图标表为空，本检查会变成空集对空集的假通过"
    offenders: list[tuple[str, list[str]]] = []
    for name, svg in _SVG_ICONS.items():
        hexes = re.findall(r"#[0-9A-Fa-f]{3,8}\b", svg)
        if hexes:
            offenders.append((name, hexes))
    assert not offenders, (
        f"这些图标写死了颜色：{offenders}。"
        "颜色只能来自 `currentColor`（渲染时替换为主题色）或 ICON_ACCENT_PLACEHOLDER（A-40）。"
    )


def test_accent_placeholder_is_used_by_the_monitor_icon() -> None:
    """带告警点的图标必须用占位符，而不是常量值。"""
    from omnicrawler.gui.design_system import ICON_ACCENT_PLACEHOLDER
    from omnicrawler.gui.icon_registry import _SVG_ICONS

    svg = _SVG_ICONS["monitor_active"]
    assert ICON_ACCENT_PLACEHOLDER in svg, svg
    assert ICON_ACCENT_PLACEHOLDER not in _SVG_ICONS["save"], "占位符不该出现在无关图标里"


def test_monitor_icon_changes_with_the_theme() -> None:
    """**A-40 的核心验收**：切主题后同一个图标的渲染结果必须改变，且用当主题的令牌色。"""
    from omnicrawler.gui.design_system import ICON_ACCENT_TOKEN, LIGHT, ThemeManager
    from omnicrawler.gui.icon_registry import IconRegistry

    app = _app()
    manager = ThemeManager.instance()
    try:
        tokens_light = manager.apply(app, "light")
        light_icon = IconRegistry.icon("monitor_active", size=24)
        light_accent = IconRegistry._resolve_color(ICON_ACCENT_TOKEN).name()

        tokens_dark = manager.apply(app, "dark")
        dark_icon = IconRegistry.icon("monitor_active", size=24)
        dark_accent = IconRegistry._resolve_color(ICON_ACCENT_TOKEN).name()
    finally:
        ThemeManager.reset()

    # `QColor.name()` 返回**小写**十六进制，而令牌里写的是大写 ⇒ 大小写无关比较
    assert light_accent.lower() == LIGHT.warning.lower(), (
        f"强调点应取主题的 warning 令牌：{light_accent} != {LIGHT.warning}"
    )
    assert light_accent.lower() != dark_accent.lower(), "两个主题取到了同一个强调色 ⇒ 说明还是写死的"
    assert light_icon.cacheKey() != dark_icon.cacheKey(), (
        "切主题后图标没有变化 ⇒ 强调点没跟着主题走（A-40 复发）"
    )
    # 主题令牌确实变了（否则上面的"变化"可能来自别的原因）
    assert tokens_light.warning.lower() != tokens_dark.warning.lower()
    assert tokens_light is not tokens_dark
