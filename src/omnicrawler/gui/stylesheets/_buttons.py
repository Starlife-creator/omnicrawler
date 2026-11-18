"""按钮全状态族、胶囊筛选器、单选复选与分割条 QSS。"""

from __future__ import annotations

from typing import Any


def render(
    tokens: Any,
    *,
    scale: int,
    RADIUS: dict[str, int],
    SPACING: dict[str, int],
    FONT_FAMILY_UI: str,
    FONT_FAMILY_MONO: str,
    scaled_font_px: Any,
    scaled_padding_px: Any,
) -> str:
    return f"""
    /* === 按钮：完整状态体系 ===
       ★ 下面 min-height 里的 `+ 2*padding` **不是重复计算，是必需余量**，不要"顺手简化"
       成只剩 `scaled_font_px("body")`：Qt 要的最小内容高度是**字体度量行高**，比名义 px
       大（实测四字体最差约 1.36 倍，如 scale160 名义 22px / 行高 30px），只写名义 px
       会每档裁字 5~8px。守卫与完整实测见
       tests/gui/test_design_system_contracts.py::test_push_button_min_height_clears_real_line_height。 */
    QPushButton {{
        background: {tokens.surface}; color: {tokens.text};
        border: 1px solid {tokens.border}; border-radius: {RADIUS["md"]}px;
        padding: {scaled_padding_px("sm", scale=scale)}px 14px;
        min-height: {scaled_font_px("body", scale=scale) + 2 * scaled_padding_px("sm", scale=scale)}px;
        font-weight: 500;
    }}
    QPushButton:hover {{ border-color: {tokens.primary}; background: {tokens.selection}; color: {tokens.primary}; }}
    QPushButton:pressed {{ background: {tokens.border}; border-color: {tokens.primary_active}; }}
    QPushButton:focus {{ border: 2px solid {tokens.primary}; padding: {scaled_padding_px("sm", scale=scale) - 1}px 13px; }}
    QPushButton:disabled {{ color: {tokens.muted}; background: {tokens.canvas}; border-color: {tokens.border}; }}

    QPushButton[primary="true"] {{
        background: {tokens.primary}; color: #FFFFFF;
        border-color: {tokens.primary}; font-weight: 600;
    }}
    QPushButton[primary="true"]:hover {{ background: {tokens.primary_hover}; border-color: {tokens.primary_hover}; color: #FFFFFF; }}
    QPushButton[primary="true"]:pressed {{ background: {tokens.primary_active}; border-color: {tokens.primary_active}; color: #FFFFFF; }}
    QPushButton[primary="true"]:disabled {{ background: {tokens.border}; color: {tokens.muted}; border-color: {tokens.border}; }}

    QPushButton[danger="true"] {{
        background: {tokens.danger}; color: #FFFFFF; border-color: {tokens.danger}; font-weight: 600;
    }}
    QPushButton[danger="true"]:hover {{ background: {tokens.danger}; color: #FFFFFF; }}

    QPushButton[homeAction="true"] {{
        text-align: left; padding: 11px 16px; font-weight: 600;
        background: {tokens.surface}; border: 1px solid {tokens.border};
        border-radius: {RADIUS["md"]}px;
    }}
    QPushButton[homeAction="true"]:hover {{
        background: {tokens.selection}; border-color: {tokens.primary}; color: {tokens.primary};
    }}

    QPushButton[success="true"] {{
        color: {tokens.success}; font-weight: 600; border: 1px solid {tokens.success}; background: {tokens.success_bg};
    }}

    QPushButton[exampleChip="true"], QPushButton#scenarioChip {{
        background: {tokens.nav}; color: {tokens.primary};
        border: 1px solid {tokens.border}; border-radius: {RADIUS["pill"]}px;
        padding: {scaled_padding_px("sm", scale=scale)}px 13px;
        font-size: {scaled_font_px("small", scale=scale)}px;
        font-weight: 600;
    }}
    QPushButton[exampleChip="true"]:hover, QPushButton#scenarioChip:hover {{
        background: {tokens.selection}; border-color: {tokens.primary};
    }}

    /* === 单选 / 复选 === */
    QRadioButton {{ spacing: 8px; padding: 4px 2px; color: {tokens.text}; }}
    QRadioButton::indicator {{ width: 16px; height: 16px; }}
    QCheckBox {{ spacing: 8px; padding: 3px 2px; color: {tokens.text}; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; }}

    /* === 分割线 === */
    QSplitter::handle {{ background: {tokens.border}; width: 1px; height: 1px; }}
    QSplitter::handle:hover {{ background: {tokens.primary}; }}

    """
