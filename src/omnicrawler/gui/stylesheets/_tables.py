"""数据表格、表头、选项卡与进度条 QSS。"""

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
    /* === 精密数据表格与列表（QTableView / QTableWidget / QTreeWidget / QHeaderView） === */
    QTableView, QTableWidget, QTreeWidget {{
        background: {tokens.surface};
        alternate-background-color: {tokens.canvas};
        color: {tokens.text};
        gridline-color: {tokens.border};
        border: 1px solid {tokens.border};
        border-radius: {RADIUS["md"]}px;
        selection-background-color: {tokens.selection};
        selection-color: {tokens.primary};
    }}
    QTableView::item, QTableWidget::item, QTreeWidget::item {{
        padding: 6px 10px;
        border-bottom: 1px solid {tokens.border};
    }}
    QTableView::item:selected, QTableWidget::item:selected, QTreeWidget::item:selected {{
        background: {tokens.selection};
        color: {tokens.primary};
    }}
    QHeaderView {{
        background: transparent; border: 0;
    }}
    QHeaderView::section {{
        background: {tokens.nav}; color: {tokens.muted};
        border: 0;
        border-bottom: 2px solid {tokens.border};
        border-right: 1px solid {tokens.border};
        padding: 8px 10px;
        font-size: {scaled_font_px("small", scale=scale)}px;
        font-weight: 700;
    }}
    QHeaderView::section:hover {{
        background: {tokens.selection}; color: {tokens.primary};
    }}
    QTableCornerButton::section {{
        background: {tokens.nav}; border: 0;
        border-bottom: 2px solid {tokens.border};
        border-right: 1px solid {tokens.border};
    }}

    /* === 标签页 === */
    QTabWidget::pane {{
        border: 1px solid {tokens.border}; border-radius: {RADIUS["md"]}px;
        background: {tokens.surface};
    }}
    QTabBar::tab {{
        padding: 8px 16px; margin-right: 4px;
        color: {tokens.muted}; background: transparent;
        border-top-left-radius: {RADIUS["sm"]}px;
        border-top-right-radius: {RADIUS["sm"]}px;
        border-bottom: 2px solid transparent;
        font-weight: 500;
    }}
    QTabBar::tab:selected {{
        background: {tokens.surface};
        color: {tokens.primary};
        border-bottom: 2px solid {tokens.primary};
        font-weight: 600;
    }}
    QTabBar::tab:hover:!selected {{
        background: {tokens.nav}; color: {tokens.text};
    }}

    /* === 进度条 === */
    QProgressBar {{
        background: {tokens.nav}; border: 1px solid {tokens.border};
        border-radius: {RADIUS["sm"]}px;
        text-align: center; min-height: 10px; color: {tokens.text};
        font-size: {scaled_font_px("caption", scale=scale)}px;
    }}
    QProgressBar::chunk {{ background: {tokens.primary}; border-radius: {RADIUS["xs"]}px; }}

    """
