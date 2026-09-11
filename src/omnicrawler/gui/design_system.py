"""OmniCrawler 2.2 visual tokens, QSS, motion and theme management.

扩展版设计系统：语义化色彩令牌 + 字体族策略 + 圆角/阴影/间距常量 +
完整 QSS（覆盖 40+ 控件，含 hover/focus/active/disabled/loading/error 状态）+
禁裸色值守卫 + 主题切换广播。
"""

from __future__ import annotations

import inspect
import logging
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass

from PySide6.QtCore import QEasingCurve, QPropertyAnimation
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QStackedWidget,
)

from .i18n import _

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 设计常量（非色彩类令牌：字体、圆角、阴影、间距）
# ---------------------------------------------------------------------------

FONT_FAMILY_UI = "PingFang SC, Microsoft YaHei, Noto Sans CJK SC, Segoe UI, sans-serif"
FONT_FAMILY_MONO = "JetBrains Mono, Consolas, Cascadia Code, Menlo, monospace"
FONT_FAMILY_DISPLAY = "PingFang SC, Microsoft YaHei, Noto Sans CJK SC, Segoe UI, sans-serif"

# 固定 rem 等价刻度（PyQt 无 rem，用 px），步距 1.125–1.2
FONT_SIZE = {
    "caption": 11,  # 0.69rem 辅助/脚注
    "small": 12,  # 0.75rem 次要标签
    "body": 14,  # 0.875rem 正文（桌面 UI 默认）
    "label": 14,  # 表单标签
    "subtitle": 15,  # 0.94rem 副标题
    "title": 18,  # 1.125rem 区块标题
    "heading": 22,  # 1.375rem 页标题
    "display": 28,  # 1.75rem 首页大标题
    "hero": 34,  # 2.125rem 英雄标题
}

# 圆角刻度
RADIUS = {"xs": 4, "sm": 6, "md": 8, "lg": 12, "xl": 16, "pill": 999}

# 间距刻度（4 的倍数）
SPACING = {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "xxl": 32}

#: 图标内嵌强调色（监控激活态）。图标 SVG 直接引用它，避免在图标字符串里写裸色值。
ICON_ACCENT_MONITOR = "#D83B01"
#: 主题不可用时的兜底强调色（仅当 ThemeManager 取令牌失败时使用，故必须零依赖）。
FALLBACK_ACCENT = "#176B87"


@dataclass(frozen=True, slots=True)
class VisualTokens:
    """语义化色彩令牌。所有 GUI 颜色必须经此获取，禁止内联十六进制。"""

    # 表面层
    canvas: str  # 应用底色
    surface: str  # 卡片/面板
    elevated: str  # 弹层/浮起
    nav: str  # 侧栏/表头底
    # 文字
    text: str  # 主文字
    muted: str  # 次要文字
    # 描边
    border: str  # 默认描边
    border_strong: str  # 聚焦/强调描边
    # 品牌色
    primary: str
    primary_hover: str
    primary_active: str
    selection: str  # 选中态背景
    # 状态色
    success: str
    success_bg: str
    warning: str
    warning_bg: str
    danger: str
    danger_bg: str
    info: str
    info_bg: str
    # 指示器状态色
    indicator_idle: str
    indicator_running: str
    indicator_finished: str
    indicator_error: str
    # 代码编辑器专用（跟随主题）
    code_bg: str
    code_fg: str
    code_border: str
    # 阴影色（带 alpha）
    shadow: str
    shadow_overlay: str  # Toast 弹层阴影
    card_shadow: str  # 卡片投影


LIGHT = VisualTokens(
    canvas="#F3F7FB",
    surface="#FFFFFF",
    elevated="#FFFFFF",
    nav="#EAF2F7",
    text="#172B3A",
    muted="#607486",
    border="#DCE6EE",
    border_strong="#B8CDD9",
    primary="#176B87",
    primary_hover="#0E7C94",
    primary_active="#0B5E74",
    selection="#DDF2F5",
    success="#23856D",
    success_bg="#E3F5EE",
    warning="#B7791F",
    warning_bg="#FCF3E0",
    danger="#C04444",
    danger_bg="#FBEAEA",
    info="#176B87",
    info_bg="#DDF2F5",
    indicator_idle="#B4B4B4",
    indicator_running="#4CAF50",
    indicator_finished="#2196F3",
    indicator_error="#F44336",
    code_bg="#1E2730",
    code_fg="#E6EDF3",
    code_border="#344957",
    shadow="rgba(23,43,58,0.12)",
    shadow_overlay="rgba(0,0,0,0.16)",
    card_shadow="rgba(26,62,78,0.13)",
)

DARK = VisualTokens(
    canvas="#111A22",
    surface="#192630",
    elevated="#20313D",
    nav="#162630",
    text="#EDF5F7",
    muted="#A6BAC6",
    border="#344957",
    border_strong="#4A6273",
    primary="#49B4C6",
    primary_hover="#69C7D5",
    primary_active="#3A9AAB",
    selection="#24444D",
    success="#54B99B",
    success_bg="#1A3329",
    warning="#E3B15C",
    warning_bg="#33291A",
    danger="#EF7777",
    danger_bg="#33201F",
    info="#49B4C6",
    info_bg="#1A2E33",
    indicator_idle="#6B7B8A",
    indicator_running="#54B99B",
    indicator_finished="#49B4C6",
    indicator_error="#EF7777",
    code_bg="#0D141A",
    code_fg="#C8D3DC",
    code_border="#2A3A47",
    shadow="rgba(0,0,0,0.40)",
    shadow_overlay="rgba(0,0,0,0.55)",
    card_shadow="rgba(0,0,0,0.30)",
)

HIGH_CONTRAST = VisualTokens(
    canvas="#000000",
    surface="#000000",
    elevated="#101010",
    nav="#000000",
    text="#FFFFFF",
    muted="#BBBBBB",
    border="#FFFFFF",
    border_strong="#FFFFFF",
    primary="#FFD600",
    primary_hover="#FFEA70",
    primary_active="#CCB500",
    selection="#153BFF",
    success="#00FF85",
    success_bg="#003319",
    warning="#FFD600",
    warning_bg="#332B00",
    danger="#FF5252",
    danger_bg="#330D0D",
    info="#FFD600",
    info_bg="#332B00",
    indicator_idle="#BBBBBB",
    indicator_running="#00FF85",
    indicator_finished="#FFD600",
    indicator_error="#FF5252",
    code_bg="#000000",
    code_fg="#FFFFFF",
    code_border="#FFFFFF",
    shadow="rgba(0,0,0,0.6)",
    shadow_overlay="rgba(0,0,0,0.75)",
    card_shadow="rgba(0,0,0,0.40)",
)


# ---------------------------------------------------------------------------
# 插件主题（本地 UI 插件注册；色值经白名单格式校验，不能注入任意 QSS）
# ---------------------------------------------------------------------------

_PLUGIN_THEMES: dict[str, tuple[str, dict[str, str]]] = {}
_VALID_TOKEN_FIELDS = frozenset(field for field in VisualTokens.__dataclass_fields__)
_TOKEN_VALUE_RE = re.compile(
    r"^#(?:[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$|^rgba\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*[0-9.]+\s*\)$"
)
_PLUGIN_THEME_ID_RE = re.compile(r"^[a-z0-9_-]{2,32}$")


def register_plugin_theme(theme_id: str, label: str, overrides: dict[str, str]) -> None:
    """注册插件主题：覆盖 VisualTokens 令牌色值。

    校验：字段名必须属于 VisualTokens，色值必须为 #RRGGBB/#RRGGBBAA 或
    rgba(r,g,b,a) 格式——插件主题无法注入任意 QSS。
    """
    theme_id = theme_id.strip().lower()
    if not _PLUGIN_THEME_ID_RE.match(theme_id):
        raise ValueError(_("主题 ID 必须是 2-32 位小写字母/数字/下划线/短横线"))
    if not label.strip():
        raise ValueError(_("主题名称不能为空"))
    if not isinstance(overrides, dict):
        raise TypeError(_("主题覆盖值必须是字典"))
    normalized: dict[str, str] = {}
    for field_name, value in overrides.items():
        if field_name not in _VALID_TOKEN_FIELDS:
            raise ValueError(_(f"未知主题令牌字段: {field_name}"))
        if not isinstance(value, str) or not _TOKEN_VALUE_RE.match(value):
            raise ValueError(_(f"非法颜色值（{field_name}={value}）：仅允许 #RRGGBB/#RRGGBBAA/rgba()"))
        normalized[field_name] = value
    _PLUGIN_THEMES[theme_id] = (label, normalized)


def unregister_plugin_theme(theme_id: str) -> None:
    """Remove one dynamically loaded plugin theme during a GUI plugin reload."""

    _PLUGIN_THEMES.pop(theme_id.strip().lower(), None)


def plugin_theme_labels() -> list[tuple[str, str]]:
    """返回 [(显示名, theme_id)]，供主题选择器追加插件主题项。"""
    return [(label, theme_id) for theme_id, (label, _) in sorted(_PLUGIN_THEMES.items())]


def plugin_theme_tokens(theme_id: str) -> VisualTokens | None:
    """按 base 令牌（跟随当前应用明暗）合并插件覆盖，返回新令牌集。"""
    entry = _PLUGIN_THEMES.get(theme_id)
    if entry is None:
        return None
    _, overrides = entry
    base_theme = "light"
    app = QApplication.instance()
    if isinstance(app, QApplication):
        if app.palette().color(QPalette.ColorRole.Window).lightness() < 128:
            base_theme = "dark"
    base = DARK if base_theme == "dark" else LIGHT
    merged = {**asdict(base), **overrides}
    return VisualTokens(**merged)


def theme_tokens(
    theme: str, *, high_contrast: bool = False, color_blind_friendly: bool = False
) -> VisualTokens:
    """根据主题名返回令牌集。色盲友好模式覆盖状态色为 Wong 学术配色。

    插件主题（register_plugin_theme 注册）优先于内置 light/dark 合并，
    且不叠加色盲/高对比模式（插件主题由作者设计，注明即可）。
    """
    plugin = plugin_theme_tokens(theme)
    if plugin is not None:
        return plugin
    if high_contrast:
        return HIGH_CONTRAST
    base = DARK if theme == "dark" else LIGHT
    if not color_blind_friendly:
        return base
    selection = "#24444D" if theme == "dark" else "#D9EFFB"
    return VisualTokens(
        base.canvas,
        base.surface,
        base.elevated,
        base.nav,
        base.text,
        base.muted,
        base.border,
        base.border_strong,
        primary="#0072B2",
        primary_hover="#56B4E9",
        primary_active="#00538F",
        selection=selection,
        success="#009E73",
        success_bg="#D9EFEC",
        warning="#E69F00",
        warning_bg="#FCEFD3",
        danger="#D55E00",
        danger_bg="#F5E0D0",
        info="#0072B2",
        info_bg="#D9EFFB",
        indicator_idle=base.indicator_idle,
        indicator_running=base.indicator_running,
        indicator_finished=base.indicator_finished,
        indicator_error=base.indicator_error,
        code_bg=base.code_bg,
        code_fg=base.code_fg,
        code_border=base.code_border,
        shadow=base.shadow,
        shadow_overlay=base.shadow_overlay,
        card_shadow=base.card_shadow,
    )


def rgba_token_to_qcolor(rgba_str: str) -> QColor:
    """Parse an ``rgba(r,g,b,a)`` token string into a :class:`QColor`.

    All alpha-bearing VisualTokens values use this format so they
    can be used with QGraphicsDropShadowEffect and QPainter.
    """
    from PySide6.QtGui import QColor

    match = re.fullmatch(r"rgba\((\d+),\s*(\d+),\s*(\d+),\s*([0-9.]+)\)", rgba_str)
    if not match:
        # 不做静默回退：输入只可能是本模块的 shadow 类令牌（实测全部为 rgba 格式），
        # 格式不对属于编程错误——静默返回一个猜测的透明黑会把它变成「看起来正常但不对」。
        raise ValueError(
            _("非法的 rgba 令牌：{0}。alpha 类令牌必须写成 rgba(r,g,b,a) 形式。").format(rgba_str)
        )
    r, g, b = int(match.group(1)), int(match.group(2)), int(match.group(3))
    a = round(float(match.group(4)) * 255)
    return QColor(r, g, b, max(0, min(255, a)))


# ---------------------------------------------------------------------------
# QSS 生成
# ---------------------------------------------------------------------------


#: 界面缩放的取值范围（**唯一来源**：accessibility.AccessibilityProfile 也取它）。
SCALE_MIN = 80
SCALE_MAX = 160


def _scale_factor(scale: int) -> float:
    return max(SCALE_MIN, min(SCALE_MAX, scale)) / 100.0


def scaled_font_px(key: str, *, scale: int | None = None) -> int:
    """按界面缩放取字号（px）。

    ``FONT_SIZE`` 是 100% 缩放下的 px 刻度；本函数是**唯一的取用入口**。

    为什么必须有它：Qt 中 QSS 的 ``font-size`` 会**覆盖** ``QApplication.setFont``，
    而主 QSS 里有 ``QMainWindow, QDialog, QWidget { font-size: …px }`` 这条几乎命中所有
    控件的规则。此前 QSS 写死绝对值、只有 app 字体跟缩放，于是「界面缩放」改了设置却
    **看不到任何变化**（实测：scale 100→150，app 字体 14→21pt，QSS 恒为 14px）。

    ``scale=None`` 时取当前已应用的缩放（未应用过按 100）。
    """
    if scale is None:
        scale = ThemeManager.instance().scale
    return max(8, round(FONT_SIZE[key] * _scale_factor(scale)))


def stylesheet(tokens: VisualTokens, *, scale: int = 100) -> str:
    """生成完整 QSS，所有颜色引用令牌，覆盖 40+ 控件与全状态。

    ``scale`` 为界面缩放百分比（80–160）；所有字号经 :func:`scaled_font_px` 换算，
    因此缩放对 QSS 控件真实生效。
    """
    return f"""
    /* === 基础容器 === */
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

    /* === 菜单栏 / 菜单 === */
    QMenuBar {{
        background: {tokens.surface}; border-bottom: 1px solid {tokens.border};
        padding: 3px 8px; spacing: 2px;
    }}
    QMenuBar::item {{ padding: 6px 10px; border-radius: {RADIUS["sm"]}px; }}
    QMenuBar::item:selected, QMenu::item:selected {{ background: {tokens.selection}; color: {tokens.primary}; }}
    QMenu {{
        background: {tokens.elevated}; border: 1px solid {tokens.border};
        padding: 6px; border-radius: {RADIUS["md"]}px;
    }}
    QMenu::item {{ padding: 7px 24px 7px 12px; border-radius: {RADIUS["xs"]}px; }}
    QMenu::separator {{ height: 1px; background: {tokens.border}; margin: 4px 8px; }}

    /* === 工具栏 / 状态栏 === */
    QToolBar {{
        background: {tokens.surface}; border: 0;
        border-bottom: 1px solid {tokens.border};
        padding: 7px 10px; spacing: 6px;
    }}
    QStatusBar {{
        background: {tokens.surface}; border-top: 1px solid {tokens.border};
        color: {tokens.muted}; padding: 2px 8px;
    }}

    /* === 侧导航 === */
    QListWidget#mainNavigation {{
        background: {tokens.nav}; border: 0;
        border-right: 1px solid {tokens.border};
        padding: {SPACING["md"]}px {SPACING["sm"]}px;
    }}
    QListWidget#mainNavigation::item {{
        padding: 11px 12px; margin: 2px 0;
        border-radius: {RADIUS["md"]}px; color: {tokens.muted};
    }}
    QListWidget#mainNavigation::item:hover {{ background: {tokens.surface}; color: {tokens.text}; }}
    QListWidget#mainNavigation::item:selected {{
        background: {tokens.selection}; color: {tokens.primary}; font-weight: 600;
    }}

    /* === 卡片 / 面板 === */
    QFrame#quickTaskCard, QFrame[card="true"] {{
        background: {tokens.surface}; border: 1px solid {tokens.border};
        border-radius: {RADIUS["lg"]}px;
    }}

    /* === 首页标题 === */
    QLabel#homeTitle {{ color: {tokens.text}; font-size: {scaled_font_px("display", scale=scale)}px; font-weight: 700; }}
    QLabel#eyebrow {{ color: {tokens.primary}; font-size: {scaled_font_px("small", scale=scale)}px; font-weight: 700; }}
    /* 区块/侧栏标题：页面与面板统一用它，避免各页面自写 font-weight/font-size 内联样式 */
    QLabel#sectionTitle {{
        color: {tokens.text};
        font-size: {scaled_font_px("subtitle", scale=scale)}px;
        font-weight: 600;
    }}

    /* === 按钮：完整状态体系 === */
    QPushButton {{
        background: {tokens.surface}; color: {tokens.text};
        border: 1px solid {tokens.border}; border-radius: {RADIUS["sm"]}px;
        padding: 7px 14px; min-height: 20px;
        font-weight: 500;
    }}
    QPushButton:hover {{ border-color: {tokens.primary}; background: {tokens.selection}; }}
    QPushButton:pressed {{ background: {tokens.border}; }}
    QPushButton:focus {{ border: 2px solid {tokens.primary}; padding: 6px 13px; }}
    QPushButton:disabled {{ color: {tokens.muted}; background: {tokens.canvas}; border-color: {tokens.border}; }}

    QPushButton[primary="true"] {{
        background: {tokens.primary}; color: #FFFFFF;
        border-color: {tokens.primary}; font-weight: 600;
    }}
    QPushButton[primary="true"]:hover {{ background: {tokens.primary_hover}; border-color: {tokens.primary_hover}; }}
    QPushButton[primary="true"]:pressed {{ background: {tokens.primary_active}; }}
    QPushButton[primary="true"]:disabled {{ background: {tokens.border}; color: {tokens.muted}; }}

    QPushButton[danger="true"] {{
        background: {tokens.danger}; color: #FFFFFF; border-color: {tokens.danger};
    }}
    QPushButton[danger="true"]:hover {{ background: {tokens.danger}; }}

    QPushButton[homeAction="true"] {{
        text-align: left; padding: 11px 14px; font-weight: 600;
        background: {tokens.surface};
    }}
    QPushButton[homeAction="true"]:hover {{ background: {tokens.selection}; border-color: {tokens.primary}; }}

    QPushButton[success="true"] {{
        color: {tokens.success}; font-weight: 600; border: 1px solid {tokens.success}; background: {tokens.success_bg};
    }}

    /* === 单选 / 复选 === */
    QRadioButton {{ spacing: 7px; padding: 4px 2px; color: {tokens.text}; }}
    QRadioButton::indicator {{ width: 16px; height: 16px; }}
    QCheckBox {{ spacing: 7px; color: {tokens.text}; }}

    /* === 分割线 === */
    QSplitter::handle {{ background: {tokens.border}; width: 1px; height: 1px; }}

    /* === 输入控件：全状态 === */
    QLineEdit, QTextEdit, QPlainTextEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTableWidget {{
        background: {tokens.surface}; color: {tokens.text};
        border: 1px solid {tokens.border}; border-radius: {RADIUS["sm"]}px;
        padding: 6px; selection-background-color: {tokens.primary};
        selection-color: #FFFFFF;
    }}
    QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
        border: 2px solid {tokens.primary}; padding: 5px;
    }}
    QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{ color: {tokens.muted}; background: {tokens.canvas}; }}
    QLineEdit[validation="error"], QTextEdit[validation="error"], QPlainTextEdit[validation="error"],
    QComboBox[validation="error"], QSpinBox[validation="error"], QDoubleSpinBox[validation="error"] {{
        border: 2px solid {tokens.danger}; border-radius: {RADIUS["sm"]}px;
    }}

    /* === 表格表头 === */
    QHeaderView::section {{
        background: {tokens.nav}; color: {tokens.muted};
        border: 0; border-bottom: 1px solid {tokens.border};
        padding: 7px; font-weight: 600;
    }}

    /* === 标签页 === */
    QTabWidget::pane {{
        border: 1px solid {tokens.border}; border-radius: {RADIUS["md"]}px;
        background: {tokens.surface};
    }}
    QTabBar::tab {{ padding: 8px 14px; color: {tokens.muted}; }}
    QTabBar::tab:selected {{
        color: {tokens.primary}; border-bottom: 2px solid {tokens.primary}; font-weight: 600;
    }}
    QTabBar::tab:hover:!selected {{ color: {tokens.text}; }}

    /* === 进度条 === */
    QProgressBar {{
        background: {tokens.border}; border: 0; border-radius: {RADIUS["xs"]}px;
        text-align: center; min-height: 10px; color: {tokens.text};
    }}
    QProgressBar::chunk {{ background: {tokens.primary}; border-radius: {RADIUS["xs"]}px; }}

    /* === 工具提示 === */
    QToolTip {{
        background: {tokens.elevated}; color: {tokens.text};
        border: 1px solid {tokens.border}; padding: 5px 8px;
        border-radius: {RADIUS["xs"]}px;
    }}

    /* === Dock === */
    QDockWidget {{ color: {tokens.text}; }}
    QDockWidget::title {{ background: {tokens.surface}; padding: 4px 8px; }}

    /* === 滚动条 === */
    QScrollBar:vertical {{
        background: transparent; width: 10px; margin: 0;
    }}
    QScrollBar::handle:vertical {{
        background: {tokens.border}; border-radius: {RADIUS["xs"]}px; min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {tokens.muted}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar:horizontal {{
        background: transparent; height: 10px; margin: 0;
    }}
    QScrollBar::handle:horizontal {{
        background: {tokens.border}; border-radius: {RADIUS["xs"]}px; min-width: 30px;
    }}
    QScrollBar::handle:horizontal:hover {{ background: {tokens.muted}; }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}

    /* === 分组框 === */
    QGroupBox {{
        border: 1px solid {tokens.border}; border-radius: {RADIUS["md"]}px;
        margin-top: 12px; padding-top: 10px;
        font-weight: 600; color: {tokens.text};
    }}
    QGroupBox::title {{
        subcontrol-origin: margin; left: 12px; padding: 0 4px;
        color: {tokens.primary};
    }}

    /* === 代码编辑器（跟随主题） === */
    QPlainTextEdit[codeEditor="true"] {{
        background: {tokens.code_bg}; color: {tokens.code_fg};
        border: 1px solid {tokens.code_border}; border-radius: {RADIUS["sm"]}px;
        font-family: {FONT_FAMILY_MONO}; font-size: {scaled_font_px("small", scale=scale)}px;
        selection-background-color: {tokens.primary};
    }}

    /* === 状态标签（通过 role 属性控制） === */
    QLabel[status="success"] {{ color: {tokens.success}; }}
    QLabel[status="warning"] {{ color: {tokens.warning}; background: {tokens.warning_bg}; }}
    QLabel[status="danger"] {{ color: {tokens.danger}; }}
    QLabel[status="muted"] {{ color: {tokens.muted}; }}
    QLabel[status="info"] {{ color: {tokens.info}; }}

    /* === 高级规则摘要条 === */
    QLabel#advancedSummary {{
        background: {tokens.warning_bg}; color: {tokens.warning};
        border-bottom: 1px solid {tokens.border}; padding: 7px 12px;
        border-radius: 0;
    }}

    /* === 占位符提示 === */
    QLabel#placeholderHint {{ color: {tokens.danger}; font-size: {scaled_font_px("caption", scale=scale)}px; }}

    /* === 空状态容器 === */
    QFrame[emptyState="true"] {{
        background: transparent; border: 2px dashed {tokens.border};
        border-radius: {RADIUS["lg"]}px; padding: {SPACING["xl"]}px;
    }}
    """


def _rgba(color: str, alpha_percent: int) -> str:
    """Convert one opaque theme token into a bounded QSS rgba color."""

    value = QColor(color)
    alpha = max(0, min(100, int(alpha_percent))) / 100
    return f"rgba({value.red()}, {value.green()}, {value.blue()}, {alpha:.2f})"


def ambient_surface_stylesheet(
    tokens: VisualTokens, *, panel_opacity: int = 88
) -> str:
    """Return a main-window-scoped translucent theme for visual backgrounds.

    The stylesheet is applied only while a host-owned background is active.
    Dialogs, menus and safety-critical transient surfaces stay opaque; passive
    containers become transparent and interactive controls retain a bounded
    panel surface so text and focus indicators remain readable.
    """

    panel = max(65, min(100, int(panel_opacity)))
    canvas = _rgba(tokens.canvas, max(28, panel - 24))
    surface = _rgba(tokens.surface, panel)
    nav = _rgba(tokens.nav, min(100, panel + 4))
    elevated = _rgba(tokens.elevated, max(92, panel))
    return f"""
    QMainWindow[ambientBackground="true"] {{ background: transparent; }}
    QMainWindow[ambientBackground="true"] QWidget {{ background-color: transparent; }}
    QMainWindow[ambientBackground="true"] QStackedWidget,
    QMainWindow[ambientBackground="true"] QScrollArea,
    QMainWindow[ambientBackground="true"] QAbstractScrollArea::viewport {{
        background-color: {canvas};
    }}
    QMainWindow[ambientBackground="true"] QToolBar,
    QMainWindow[ambientBackground="true"] QStatusBar,
    QMainWindow[ambientBackground="true"] QMenuBar,
    QMainWindow[ambientBackground="true"] QDockWidget::title {{
        background-color: {surface};
    }}
    QMainWindow[ambientBackground="true"] QFrame[card="true"],
    QMainWindow[ambientBackground="true"] QFrame#quickTaskCard,
    QMainWindow[ambientBackground="true"] QPushButton,
    QMainWindow[ambientBackground="true"] QLineEdit,
    QMainWindow[ambientBackground="true"] QTextEdit,
    QMainWindow[ambientBackground="true"] QPlainTextEdit,
    QMainWindow[ambientBackground="true"] QComboBox,
    QMainWindow[ambientBackground="true"] QSpinBox,
    QMainWindow[ambientBackground="true"] QDoubleSpinBox,
    QMainWindow[ambientBackground="true"] QListWidget,
    QMainWindow[ambientBackground="true"] QTreeWidget,
    QMainWindow[ambientBackground="true"] QTableWidget,
    QMainWindow[ambientBackground="true"] QTabWidget::pane,
    QMainWindow[ambientBackground="true"] QGroupBox {{
        background-color: {surface};
    }}
    QMainWindow[ambientBackground="true"] QListWidget#mainNavigation {{
        background-color: {nav};
    }}
    QMainWindow[ambientBackground="true"] QMenu,
    QMainWindow[ambientBackground="true"] QDialog,
    QMainWindow[ambientBackground="true"] QMessageBox {{
        background-color: {elevated};
    }}
    """


# ---------------------------------------------------------------------------
# 禁裸色值守卫
# ---------------------------------------------------------------------------

_HEX_PATTERN = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")

# 令牌白名单（允许在 setStyleSheet 中出现的十六进制值）
# 注意：**必须**遍历实际会用到的全部调色板，而不是只遍历 LIGHT/DARK/HIGH_CONTRAST 三个
# 常量——色盲友好主题是在 theme_tokens() 里另行构造的（§A-20）。此前只遍历三个常量，
# 导致「开启色盲友好 + 开发期严格色值检查」时 stylesheet 里出现 33~37 处「非法裸 hex」
# 而直接抛 ValueError（实测三种主题全部崩溃）。
_TOKEN_HEX_WHITELIST: set[str] = set()
_PALETTES = [
    theme_tokens(_name, color_blind_friendly=_cb)
    for _name in ("light", "dark", "high_contrast")
    for _cb in (False, True)
]
for _t in _PALETTES:
    for _f in (
        _t.canvas,
        _t.surface,
        _t.elevated,
        _t.nav,
        _t.text,
        _t.muted,
        _t.border,
        _t.border_strong,
        _t.primary,
        _t.primary_hover,
        _t.primary_active,
        _t.selection,
        _t.success,
        _t.warning,
        _t.danger,
        _t.info,
        _t.success_bg,
        _t.warning_bg,
        _t.danger_bg,
        _t.info_bg,
        _t.indicator_idle,
        _t.indicator_running,
        _t.indicator_finished,
        _t.indicator_error,
        _t.code_bg,
        _t.code_fg,
        _t.code_border,
    ):
        _TOKEN_HEX_WHITELIST.add(_f.upper())
_TOKEN_HEX_WHITELIST.update({"#FFFFFF", "#ffffff", "#FFF", "#fff"})


#: Qt QSS **不实现**的 CSS 属性：写了不会报错，会**静默无效**。
#: 只收「确定不支持」的（依据 Qt 样式表参考的属性清单），宁缺勿滥以免误报。
_UNSUPPORTED_QSS_PROPERTIES = frozenset(
    {
        "outline", "outline-color", "outline-style", "outline-width", "outline-offset",
        "letter-spacing", "word-spacing", "line-height", "text-transform", "white-space",
        "transition", "animation", "box-shadow", "text-shadow",
        "gap", "column-gap", "row-gap", "box-sizing", "transform", "opacity", "z-index",
        "display", "float", "flex", "flex-direction",
    }
)

#: Qt QSS **不实现**的伪状态。`:focus-visible` 是重灾区——写它等于没写焦点样式，
#: 而键盘用户因此看不到焦点（§A-18）。
_UNSUPPORTED_QSS_PSEUDO = frozenset(
    {
        "focus-visible", "focus-within", "first-child", "last-child", "only-child",
        "nth-child", "nth-of-type", "visited", "link", "target", "root",
        "placeholder-shown", "before", "after",
    }
)

_QSS_BLOCK = re.compile(r"\{([^{}]*)\}", re.S)
_QSS_DECL = re.compile(r"^\s*([a-z-]+)\s*:", re.M)


def assert_qss_supported(qss: str, *, context: str = "") -> None:
    """守卫：QSS 里不得出现 Qt 不支持的属性或伪状态。

    这类写法的共同点是**不报错、静默无效**——本项目曾因此让整块「全局焦点可视化」
    变成死规则（`:focus-visible` + `outline` / `outline-offset`），键盘用户看不到焦点，
    而没有任何测试会失败（§A-18）。把它变成会抛错的守卫，才是真的修掉。
    """
    body = re.sub(r"/\*.*?\*/", " ", qss, flags=re.S)

    bad_props: list[str] = []
    for block in _QSS_BLOCK.findall(body):
        bad_props.extend(p for p in _QSS_DECL.findall(block) if p in _UNSUPPORTED_QSS_PROPERTIES)
    bad_pseudo = sorted(
        {m.group(1) for m in re.finditer(r":([a-z-]+)", body) if m.group(1) in _UNSUPPORTED_QSS_PSEUDO}
    )

    if bad_props or bad_pseudo:
        parts: list[str] = []
        if bad_props:
            parts.append(_("不支持的属性：") + ", ".join(sorted(set(bad_props))))
        if bad_pseudo:
            parts.append(_("不支持的伪状态：") + ", ".join(":" + name for name in bad_pseudo))
        raise ValueError(
            _(f"QSS 含 Qt 不实现的选择器/属性（{context}）：") + "；".join(parts)
            + _("。这类写法不报错但静默无效，请改用 Qt 支持的等价属性。")
        )


def assert_no_raw_hex(qss_or_style: str, *, context: str = "") -> None:
    """守卫：扫描样式串中的裸十六进制色值，非令牌白名单则抛 ValueError。

    在开发期（assert_optimize=True）调用，阻止新代码引入硬编码颜色。
    生产期可跳过以避免性能损耗。
    """
    import os

    if os.environ.get("OMNICRAWL_GUI_STRICT_HEX", "") not in ("1", "true", "yes"):
        return
    offenders: list[str] = []
    for match in _HEX_PATTERN.finditer(qss_or_style):
        val = match.group()
        if val.upper() not in _TOKEN_HEX_WHITELIST:
            offenders.append(val)
    if offenders:
        raise ValueError(
            _(f"样式串包含非法十六进制颜色值（{context}），发现 {len(offenders)} 处裸十六进制颜色值: ")
            + _(f"{', '.join(offenders[:8])}。请改用 VisualTokens 令牌。")
        )


# ---------------------------------------------------------------------------
# 字体应用
# ---------------------------------------------------------------------------


def apply_font_strategy(app: QApplication, *, scale: int = 100) -> None:
    """应用字体族策略。scale 为百分比（80–160）。

    单位口径统一为 **px**：``FONT_SIZE`` 是 px 刻度（见其定义处的说明），QSS 也用 px，
    因此这里必须用 :meth:`QFont.setPixelSize`——此前用 ``setPointSize`` 把 px 数字当成
    「点」应用，同一条令牌在两条路径下渲染成不同大小（§A-23）。
    """
    font = QFont()
    font.setFamilies(FONT_FAMILY_UI.split(", "))
    font.setPixelSize(scaled_font_px("body", scale=scale))
    app.setFont(font)


# ---------------------------------------------------------------------------
# 主题管理器（支持广播）
# ---------------------------------------------------------------------------


#: 信号代理的回调类型：Qt 槽可以是任意可调用对象。
_Callback = Callable[..., None]


def _accepts_no_args(callback: _Callback) -> bool:
    """该回调能否以「零实参」调用（用于区分实参裁剪与回调内部 TypeError）。"""
    try:
        signature = inspect.signature(callback)
    except (TypeError, ValueError):
        return True  # 取不到签名（C 实现/内建）→ 保守按可裁剪处理
    for parameter in signature.parameters.values():
        if parameter.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if parameter.default is inspect.Parameter.empty and parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return False
    return True


class _SignalProxy:
    """纯 Python 信号代理，模拟 Qt Signal 的 .connect()/.disconnect()/.emit() API。

    不依赖 QObject/C++ 对象，避免 QApplication 销毁后信号失效。
    当回调的目标 QObject 被删除时自动清理（通过 not shiboken6.isValid() 预检
    和 RuntimeError 捕获双重保障）。
    内置重入保护，防止回调链触发递归 emit 导致无限循环。
    """

    def __init__(self) -> None:
        self._callbacks: list[_Callback] = []
        self._emitting = False

    def connect(self, callback: _Callback) -> None:
        self._callbacks.append(callback)

    def disconnect(self, callback: _Callback) -> None:
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def emit(self, *args: object) -> None:
        if self._emitting:
            return  # 重入保护：回调执行期间的 emit 被静默丢弃
        self._emitting = True
        try:
            self._emit_to_all(*args)
        finally:
            # 必须放 finally：此前若回调抛出未捕获的 BaseException（如 Ctrl-C），
            # `_emitting` 会永久为 True —— 信号总线从此**完全静默**。
            self._emitting = False

    def _emit_to_all(self, *args: object) -> None:
        try:
            import shiboken6 as _sip
        except ImportError:
            _sip = None  # type: ignore[assignment]  # 无 PySide6 时降级
        dead: list[_Callback] = []
        for callback in list(self._callbacks):
            # 检查 bound method 的目标对象是否已被 C++ 析构
            target = getattr(callback, "__self__", None)
            if target is not None and _sip is not None:
                try:
                    if not _sip.isValid(target):
                        dead.append(callback)
                        continue
                except (TypeError, AttributeError):
                    pass
            try:
                callback(*args)
            except RuntimeError:
                dead.append(callback)  # C++ 对象已销毁：摘除该订阅者
            except TypeError:
                # Qt 会按槽的形参个数裁剪实参，纯 Python 不能，故这里退化为无参重试。
                # 但必须区分两种 TypeError：
                #   ① 实参多于形参（真的需要裁剪）→ 无参重试；
                #   ② **回调内部自己**抛 TypeError（真 bug）→ 不能伪装成 ①，否则被降级掩盖。
                if _accepts_no_args(callback):
                    try:
                        callback()
                    except Exception:
                        logger.warning(_("信号回调无参重试仍失败"), exc_info=True)
                else:
                    logger.warning(_("信号回调调用失败（回调内部 TypeError）"), exc_info=True)
            except Exception:
                logger.warning(_("信号回调调用失败"), exc_info=True)
        for callback in dead:
            try:
                self._callbacks.remove(callback)
            except ValueError:
                pass


class ThemeManager:
    """主题管理：应用令牌 + 字体 + 广播变更信号，供组件监听刷新。

    纯 Python 实现，不继承 QObject，从根本上消除 C++ 对象生命周期导致的崩溃。
    使用 _SignalProxy 替代 Signal，API 完全兼容（.connect()/.disconnect()/.emit()）。
    """

    _instance: ThemeManager | None = None

    def __init__(self) -> None:
        self._current_theme: str = "light"
        self._current_tokens: VisualTokens = LIGHT
        self._app: QApplication | None = None
        self._qss_cache: str | None = None
        self._current_scale = 100
        # 键 = （令牌哈希, 缩放）：两者任一变化都要重建 QSS。
        self._qss_cache_key: tuple[int, int] | None = None
        self._theme_changed_signal = _SignalProxy()

    @classmethod
    def instance(cls) -> ThemeManager:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Clear the singleton instance."""
        cls._instance = None

    @property
    def tokens(self) -> VisualTokens:
        return self._current_tokens

    @property
    def scale(self) -> int:
        """当前已应用的界面缩放百分比（未应用过为 100）。"""
        return self._current_scale

    @property
    def theme_name(self) -> str:
        return self._current_theme

    @property
    def theme_changed(self) -> _SignalProxy:
        """Qt 兼容的信号代理，支持 .connect()/.disconnect()/.emit()。"""
        return self._theme_changed_signal

    def apply(
        self,
        app: QApplication,
        theme: str,
        *,
        high_contrast: bool = False,
        color_blind_friendly: bool = False,
        scale: int = 100,
    ) -> VisualTokens:
        """应用主题到 app，并广播令牌变更。"""
        self._app = app
        tokens = theme_tokens(theme, high_contrast=high_contrast, color_blind_friendly=color_blind_friendly)
        self._current_theme = theme
        self._current_tokens = tokens
        self._current_scale = scale

        # 调色板
        palette = QPalette()
        palette.setColor(QPalette.ColorRole.Window, QColor(tokens.canvas))
        palette.setColor(QPalette.ColorRole.WindowText, QColor(tokens.text))
        palette.setColor(QPalette.ColorRole.Base, QColor(tokens.surface))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor(tokens.nav))
        palette.setColor(QPalette.ColorRole.Text, QColor(tokens.text))
        palette.setColor(QPalette.ColorRole.Button, QColor(tokens.surface))
        palette.setColor(QPalette.ColorRole.ButtonText, QColor(tokens.text))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(tokens.primary))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(tokens.muted))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(tokens.elevated))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor(tokens.text))
        app.setPalette(palette)

        # 字体
        apply_font_strategy(app, scale=scale)

        # QSS (with cache — regenerated only when tokens change)
        # 缓存键必须含 scale：否则只改缩放时令牌哈希不变，会直接复用旧 QSS，
        # 缩放看起来「设置了但没反应」。
        tk_hash = (hash(tuple(asdict(tokens).items())), scale)
        if self._qss_cache is None or self._qss_cache_key != tk_hash:
            qss = stylesheet(tokens, scale=scale)
            assert_no_raw_hex(qss, context="design_system.stylesheet")
            assert_qss_supported(qss)
            self._qss_cache = qss
            self._qss_cache_key = tk_hash
        app.setStyleSheet(self._qss_cache)

        # 标记 reduced-motion 属性（供 AmbientHero 等读取）
        app.setProperty("omnicrawlerTheme", theme)
        app.setProperty("omnicrawlerTokens", tokens)

        # 广播
        self.theme_changed.emit(theme, tokens)
        return tokens


# 向后兼容：保留原函数签名
def apply_design_system(
    app: QApplication,
    theme: str,
    *,
    high_contrast: bool = False,
    color_blind_friendly: bool = False,
    scale: int = 100,
) -> VisualTokens:
    """向后兼容入口，委托 ThemeManager。"""
    return ThemeManager.instance().apply(
        app,
        theme,
        high_contrast=high_contrast,
        color_blind_friendly=color_blind_friendly,
        scale=scale,
    )


# ---------------------------------------------------------------------------
# 页面切换动画
# ---------------------------------------------------------------------------


class PageTransitionController:
    """短促非阻塞淡入；受无障碍 reduced-motion 开关控制。"""

    def __init__(self, stack: QStackedWidget, *, reduced_motion: bool = False) -> None:
        self.stack = stack
        self.reduced_motion = reduced_motion
        self._animation: QPropertyAnimation | None = None

    def show(self, index: int) -> None:
        # A transition can be superseded before its delayed ``finished`` signal
        # is delivered.  Stop it first: otherwise the old callback can address
        # a page/effect Qt has already destroyed during navigation or teardown.
        if self._animation is not None:
            try:
                self._animation.stop()
            except RuntimeError:
                # Its parent page may already have been deleted by Qt.
                pass
            self._animation = None
        self.stack.setCurrentIndex(index)
        page = self.stack.currentWidget()
        if page is None or self.reduced_motion:
            return
        effect = QGraphicsOpacityEffect(page)
        page.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", page)
        animation.setDuration(160)
        animation.setStartValue(0.35)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)

        def _finish_transition() -> None:
            """Clear only the live effect; page deletion is normal in tests/navigation."""
            try:
                import shiboken6

                if not shiboken6.isValid(page) or not shiboken6.isValid(effect):
                    return
                if page.graphicsEffect() is effect:
                    # PySide6 存根不接受 None，但运行时传 None 是官方清除特效方式
                    page.setGraphicsEffect(None)  # type: ignore[arg-type]
            except (ImportError, RuntimeError, TypeError):
                # Qt may delete either wrapper before the queued completion runs.
                pass
            finally:
                if self._animation is animation:
                    self._animation = None

        animation.finished.connect(_finish_transition)
        self._animation = animation
        animation.start()
