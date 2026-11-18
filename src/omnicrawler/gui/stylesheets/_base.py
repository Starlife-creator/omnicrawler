"""基础容器、焦点环、滚动条、代码区与状态空态 QSS。"""

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
    /* === 基础容器（Swiss Botanical Engineering 精密墨绿工作台） === */
    QMainWindow, QDialog, QWidget {{
        background-color: {tokens.canvas}; color: {tokens.text};
        font-family: {FONT_FAMILY_UI};
        font-size: {scaled_font_px("body", scale=scale)}px;
    }}
    QLabel, QRadioButton, QCheckBox {{ background: transparent; }}
    QLabel#muted, QLabel[role="muted"] {{ color: {tokens.muted}; }}

    /* === 全局焦点可视化（§A-18）===
       只用 Qt **实现**的伪状态与属性：`:focus-visible` 与 `outline` / `outline-offset`
       都不被 Qt 支持，原先这一整块是死规则 —— 键盘用户因此看不到焦点。
       改法：按控件类别用 `:focus`，并以「边框 +1px ⇒ 内边距 −1px」加粗焦点框，
       **保证控件外框尺寸不变**（否则聚焦瞬间内容会跳 1px，见 §A-31）。 */
    QListWidget:focus, QTreeWidget:focus, QTableWidget:focus, QTableView:focus,
    QTextEdit:focus, QPlainTextEdit:focus {{
        border-color: {tokens.border_strong};
    }}
    QTabBar::tab:focus {{ border-color: {tokens.border_strong}; color: {tokens.primary}; }}

    /* === 极简圆角滚动条（6px 滑块） === */
    QScrollBar:vertical {{
        background: transparent; width: 8px; margin: 2px 1px;
    }}
    QScrollBar::handle:vertical {{
        background: {tokens.border}; border-radius: 3px; min-height: 32px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {tokens.border_strong}; }}
    QScrollBar::handle:vertical:pressed {{ background: {tokens.primary}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
    QScrollBar:horizontal {{
        background: transparent; height: 8px; margin: 1px 2px;
    }}
    QScrollBar::handle:horizontal {{
        background: {tokens.border}; border-radius: 3px; min-width: 32px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {tokens.border_strong}; }}
    QScrollBar::handle:horizontal:pressed {{ background: {tokens.primary}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

    /* === 代码编辑器（跟随主题） === */
    QPlainTextEdit[codeEditor="true"] {{
        background: {tokens.code_bg}; color: {tokens.code_fg};
        border: 1px solid {tokens.code_border}; border-radius: {RADIUS["md"]}px;
        font-family: {FONT_FAMILY_MONO}; font-size: {scaled_font_px("small", scale=scale)}px;
        selection-background-color: {tokens.primary};
    }}

    /* === 状态标签（通过 role 属性控制） === */
    QLabel[status="success"], QLabel[status="ok"] {{ color: {tokens.success}; }}
    QLabel[status="warning"] {{ color: {tokens.warning}; background: {tokens.warning_bg}; }}
    QLabel[status="danger"] {{ color: {tokens.danger}; }}
    QLabel[status="muted"] {{ color: {tokens.muted}; }}
    QLabel[status="info"] {{ color: {tokens.info}; }}

    /* === 高级规则摘要条 / 结果字段完整度摘要条 === */
    QLabel#advancedSummary {{
        background: {tokens.warning_bg}; color: {tokens.warning};
        border-bottom: 1px solid {tokens.border}; padding: 8px 14px;
        border-radius: 0; font-weight: 500;
    }}
    QLabel#completenessSummary {{
        border: 1px solid {tokens.border};
        border-left: 4px solid {tokens.border_strong};
        border-radius: {RADIUS["md"]}px;
        padding: 6px 12px; font-size: {scaled_font_px("small", scale=scale)}px;
        background: {tokens.surface}; font-weight: 500;
    }}
    QLabel#completenessSummary[status="success"], QLabel#completenessSummary[status="ok"] {{
        color: {tokens.success}; background: {tokens.success_bg};
        border-color: {tokens.border}; border-left: 4px solid {tokens.success};
    }}
    QLabel#completenessSummary[status="warning"] {{
        color: {tokens.warning}; background: {tokens.warning_bg};
        border-color: {tokens.border}; border-left: 4px solid {tokens.warning};
    }}
    QLabel#completenessSummary[status="danger"] {{
        color: {tokens.danger}; background: {tokens.danger_bg};
        border-color: {tokens.border}; border-left: 4px solid {tokens.danger};
    }}
    QLabel#completenessSummary[status="muted"] {{
        color: {tokens.muted}; background: {tokens.nav};
        border-color: {tokens.border}; border-left: 4px solid {tokens.border_strong};
    }}

    /* === 占位符提示 === */
    QLabel#placeholderHint {{ color: {tokens.danger}; font-size: {scaled_font_px("caption", scale=scale)}px; }}

    /* === 空状态容器 === */
    QFrame[emptyState="true"] {{
        background: transparent; border: 2px dashed {tokens.border};
        border-radius: {RADIUS["lg"]}px; padding: {SPACING["xl"]}px;
    }}
    """
