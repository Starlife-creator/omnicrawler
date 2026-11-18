"""输入控件、卡片、Toast、分组框与提示浮层 QSS。"""

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
    /* === 卡片 / 面板 === */
    QFrame#quickTaskCard, QFrame[card="true"] {{
        background: {tokens.surface}; border: 1px solid {tokens.border};
        border-radius: {RADIUS["lg"]}px;
    }}
    QFrame#recentTaskCard {{
        background: {tokens.surface}; border: 1px solid {tokens.border};
        border-radius: {RADIUS["md"]}px;
    }}
    QFrame#recentTaskCard:hover {{
        border-color: {tokens.border_strong};
    }}

    /* === Toast 浮动通知 === */
    QFrame#toast {{
        background: {tokens.elevated}; color: {tokens.text};
        border: 1px solid {tokens.border_strong};
        border-radius: {RADIUS["md"]}px;
    }}
    QFrame#toast[toastKind="success"] {{ border-left: 4px solid {tokens.success}; }}
    QFrame#toast[toastKind="warning"] {{ border-left: 4px solid {tokens.warning}; }}
    QFrame#toast[toastKind="error"] {{ border-left: 4px solid {tokens.danger}; }}
    QFrame#toast[toastKind="info"] {{ border-left: 4px solid {tokens.info}; }}
    QLabel#toastMessage {{ color: {tokens.text}; font-weight: 500; }}
    QPushButton#toastAction {{ color: {tokens.primary}; font-weight: 600; border: 0; background: transparent; }}

    /* === 首页标题 === */
    /* V2：三层拉开层级对比 —— eyebrow(small 12) → lead(subtitle 15) → 标题(hero 34)。
       此前标题用 display(28)、副标题走 muted 角色（body 14），三级几乎挨在一起。 */
    QLabel#homeTitle {{ color: {tokens.text}; font-size: {scaled_font_px("hero", scale=scale)}px; font-weight: 700; }}
    QLabel#eyebrow {{ color: {tokens.primary}; font-size: {scaled_font_px("small", scale=scale)}px; font-weight: 700; }}
    QLabel#homeLead {{ color: {tokens.muted}; font-size: {scaled_font_px("subtitle", scale=scale)}px; }}
    /* 区块/侧栏标题：页面与面板统一用它，避免各页面自写 font-weight/font-size 内联样式 */
    QLabel#sectionTitle {{
        color: {tokens.text};
        font-size: {scaled_font_px("subtitle", scale=scale)}px;
        font-weight: 600;
    }}

    /* === 输入控件：全状态（保持 1px→2px 焦点内边距补偿，外框零跳动） === */
    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {tokens.surface}; color: {tokens.text};
        border: 1px solid {tokens.border}; border-radius: {RADIUS["md"]}px;
        padding: 6px 10px; selection-background-color: {tokens.primary};
        selection-color: #FFFFFF;
    }}
    QLineEdit:hover, QTextEdit:hover, QPlainTextEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover {{
        border-color: {tokens.border_strong};
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 2px solid {tokens.primary}; padding: 5px 9px;
    }}
    QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
        color: {tokens.muted}; background: {tokens.canvas}; border-color: {tokens.border};
    }}
    QLineEdit[validation="error"], QTextEdit[validation="error"], QPlainTextEdit[validation="error"],
    QComboBox[validation="error"], QSpinBox[validation="error"], QDoubleSpinBox[validation="error"] {{
        border: 2px solid {tokens.danger}; border-radius: {RADIUS["md"]}px;
    }}
    QComboBox QAbstractItemView {{
        background: {tokens.elevated}; color: {tokens.text};
        border: 1px solid {tokens.border_strong};
        selection-background-color: {tokens.selection};
        selection-color: {tokens.primary};
        padding: 4px;
    }}

    /* === 工具提示 === */
    QToolTip {{
        background: {tokens.elevated}; color: {tokens.text};
        border: 1px solid {tokens.border_strong}; padding: 6px 10px;
        border-radius: {RADIUS["sm"]}px;
    }}

    /* === Dock === */
    QDockWidget {{ color: {tokens.text}; }}
    QDockWidget::title {{
        background: {tokens.nav}; border-bottom: 1px solid {tokens.border};
        padding: 6px 10px; font-weight: 600;
    }}

    /* === 卡片式分组框 === */
    QGroupBox {{
        background: {tokens.surface};
        border: 1px solid {tokens.border}; border-radius: {RADIUS["lg"]}px;
        margin-top: 14px; padding: 14px 12px 10px 12px;
        font-weight: 600; color: {tokens.text};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin; left: 14px; padding: 1px 8px;
        background: {tokens.nav}; border: 1px solid {tokens.border};
        border-radius: {RADIUS["xs"]}px;
        color: {tokens.primary};
        font-size: {scaled_font_px("small", scale=scale)}px;
    }}

    """
