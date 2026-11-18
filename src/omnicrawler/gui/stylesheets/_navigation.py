"""菜单栏、工具栏、状态栏、侧栏与命令面板 QSS。"""

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
    /* === 菜单栏 / 菜单 === */
    QMenuBar {{
        background: {tokens.surface}; border-bottom: 1px solid {tokens.border};
        padding: 4px 10px; spacing: 3px;
    }}
    QMenuBar::item {{
        padding: 6px 11px; border-radius: {RADIUS["sm"]}px; color: {tokens.text};
    }}
    QMenuBar::item:selected, QMenu::item:selected {{
        background: {tokens.selection}; color: {tokens.primary}; font-weight: 600;
    }}
    QMenu {{
        background: {tokens.elevated}; border: 1px solid {tokens.border_strong};
        padding: 6px; border-radius: {RADIUS["md"]}px;
    }}
    QMenu::item {{ padding: 7px 26px 7px 12px; border-radius: {RADIUS["sm"]}px; }}
    QMenu::item:disabled {{ color: {tokens.muted}; background: transparent; }}
    QMenu::separator {{ height: 1px; background: {tokens.border}; margin: 5px 8px; }}

    /* === 工具栏 / 状态栏 === */
    QToolBar {{
        background: {tokens.surface}; border: 0;
        border-bottom: 1px solid {tokens.border};
        padding: 6px 12px; spacing: 8px;
    }}
    QStatusBar {{
        background: {tokens.surface}; border-top: 1px solid {tokens.border};
        color: {tokens.muted}; padding: 3px 10px;
        font-size: {scaled_font_px("small", scale=scale)}px;
    }}

    /* === 侧导航（悬浮胶囊 + Spotlight 搜索触发器） === */
    QWidget#navSidebar {{
        background: {tokens.nav};
        border-right: 1px solid {tokens.border};
    }}
    QPushButton#quickJumpButton {{
        background: {tokens.surface}; color: {tokens.muted};
        border: 1px solid {tokens.border}; border-radius: {RADIUS["md"]}px;
        padding: {scaled_padding_px("sm", scale=scale)}px 12px;
        margin: 10px 8px 4px 8px;
        text-align: left; font-size: {scaled_font_px("small", scale=scale)}px;
        font-weight: 500;
    }}
    QPushButton#quickJumpButton:hover {{
        border-color: {tokens.primary}; color: {tokens.primary};
        background: {tokens.selection};
    }}
    QPushButton#quickJumpButton:pressed {{
        border-color: {tokens.primary_active}; background: {tokens.selection};
    }}
    QListWidget#mainNavigation {{
        background: {tokens.nav}; border: 0;
        border-right: 1px solid {tokens.border};
        padding: 6px 0px;
    }}
    QListWidget#mainNavigation::item {{
        padding: 8px 12px; margin: 2px 8px;
        border-radius: {RADIUS["md"]}px;
        border-left: 3px solid transparent;
        color: {tokens.muted};
    }}
    QListWidget#mainNavigation::item:hover {{
        background: {tokens.surface}; color: {tokens.text};
    }}
    QListWidget#mainNavigation::item:selected {{
        background: {tokens.selection}; color: {tokens.primary};
        border-left: 3px solid {tokens.primary};
        font-weight: 600;
    }}
    QListWidget#mainNavigation::item:disabled {{
        background: transparent; border-left: 0;
        color: {tokens.muted}; font-weight: 700;
        font-size: {scaled_font_px("caption", scale=scale)}px;
        padding: 12px 12px 4px 12px; margin: 2px 6px 0px 6px;
    }}

    /* === 全局命令面板（Ctrl+K Spotlight 模态窗） === */
    QDialog#commandPaletteDialog {{
        background: {tokens.elevated};
        border: 1px solid {tokens.border_strong};
        border-radius: {RADIUS["lg"]}px;
    }}
    QFrame#commandPaletteSearchFrame {{
        background: {tokens.surface};
        border: 1px solid {tokens.border_strong};
        border-bottom: 2px solid {tokens.primary};
        border-radius: {RADIUS["md"]}px;
    }}
    QLabel#commandPaletteBadge {{
        color: {tokens.primary}; background: {tokens.selection};
        border: 1px solid {tokens.border}; border-radius: {RADIUS["xs"]}px;
        padding: 2px 7px; font-family: {FONT_FAMILY_MONO};
        font-size: {scaled_font_px("caption", scale=scale)}px; font-weight: 700;
    }}
    QLabel#commandPaletteKeycap {{
        color: {tokens.muted}; background: {tokens.nav};
        border: 1px solid {tokens.border}; border-radius: {RADIUS["xs"]}px;
        padding: 2px 6px; font-family: {FONT_FAMILY_MONO};
        font-size: {scaled_font_px("caption", scale=scale)}px;
    }}
    QLineEdit#commandPaletteInput {{
        background: transparent; color: {tokens.text};
        border: 0; padding: 8px 6px;
        font-size: {scaled_font_px("subtitle", scale=scale)}px;
    }}
    QLineEdit#commandPaletteInput:focus {{
        border: 0; padding: 8px 6px;
    }}
    QListWidget#commandPaletteList {{
        background: {tokens.surface}; border: 1px solid {tokens.border};
        border-radius: {RADIUS["md"]}px; padding: 6px;
    }}
    QListWidget#commandPaletteList::item {{
        padding: 10px 14px; margin: 2px 0;
        border-radius: {RADIUS["sm"]}px;
        border-left: 3px solid transparent;
        color: {tokens.text};
    }}
    QListWidget#commandPaletteList::item:hover {{
        background: {tokens.nav};
    }}
    QListWidget#commandPaletteList::item:selected {{
        background: {tokens.selection}; color: {tokens.primary};
        border-left: 3px solid {tokens.primary}; font-weight: 600;
    }}
    QFrame#commandPaletteFooter {{
        background: {tokens.nav}; border: 1px solid {tokens.border};
        border-radius: {RADIUS["sm"]}px;
    }}

    """
