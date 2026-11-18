"""全局命令面板与快速跳转对话框（Ctrl+K）。

能力感知契约：
- 页面列表实时调用 ``visible_entries(navigation_capabilities())`` 过滤，未安装能力
  （如未安装 ``document-workbench`` 或无 QML 运行时的 ``qml_showcase``）绝不作为死入口出现；
- 同时聚合全局常用动作（运行/试跑、停止、保存、YAML 切换、模板库、刷新结果、
  格式化 YAML、请勿打扰、深浅色主题切换、帮助中心）；
- 支持多关键词（空格分词）子串与英文/拼音检索、上下方向键导航、回车执行、Esc 关闭。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.capabilities import navigation_capabilities
from .i18n import _
from .navigation import NavKey, visible_entries
from .settings import AppSettings


@dataclass(frozen=True, slots=True)
class PaletteCommand:
    """命令面板中的单条可检索项（导航页面或全局动作）。"""

    command_id: str
    title: str
    category: str
    keywords: tuple[str, ...] = ()
    shortcut: str = ""
    nav_key: str | None = None
    callback: Callable[[], None] | None = None


_NAV_KEYWORDS: dict[str, tuple[str, ...]] = {
    NavKey.HOME: ("home", "start", "index", "shouye"),
    NavKey.WORKSPACE: ("workspace", "canvas", "task", "wizard", "gongzuotai", "renwu"),
    NavKey.MONITOR: ("monitor", "run", "history", "log", "yunxing", "lishi", "rizhi"),
    NavKey.RESULTS: ("results", "result", "table", "csv", "review", "jieguo", "fuhe"),
    NavKey.CHANGE_MONITOR: ("change", "watch", "alert", "monitor", "biangeng", "jiankong"),
    NavKey.PDF_WORKBENCH: ("pdf", "doc", "document", "ocr", "wendang"),
    NavKey.CONVERT_TOOL: ("convert", "convertx", "format", "geshi", "huzhuan"),
    NavKey.SCENE: ("scene", "template", "slot", "changjing", "moban"),
    NavKey.LOGIN_SESSION: ("login", "session", "cookie", "auth", "denglu", "huihua"),
    NavKey.QML_SHOWCASE: ("qml", "showcase", "chuchuang"),
    NavKey.YAML_EDITOR: ("yaml", "editor", "code", "config", "bianjiqi"),
    NavKey.EVIDENCE: ("evidence", "proof", "audit", "zhengju"),
    NavKey.PLUGIN_MARKET: ("market", "plugin", "store", "extension", "shichang", "chajian"),
    NavKey.DEVELOPER: ("developer", "dev", "debug", "inspector", "kaifazhe"),
}


def default_nav_labels() -> dict[str, str]:
    """默认导航标签映射（与 ``central_area._NAV_LABELS`` 对齐，测试或独立调用可回退）。"""
    return {
        NavKey.GROUP_WORK: _("工作"),
        NavKey.HOME: "⌂ " + _("首页"),
        NavKey.WORKSPACE: "⚙ " + _("任务工作台"),
        NavKey.MONITOR: "▶ " + _("运行与历史"),
        NavKey.RESULTS: "📊 " + _("结果与复核"),
        NavKey.GROUP_AUTOMATION: _("自动化"),
        NavKey.CHANGE_MONITOR: "🔔 " + _("变更监控"),
        NavKey.GROUP_TOOLS: _("工具"),
        NavKey.PDF_WORKBENCH: "📄 " + _("文档工作台"),
        NavKey.CONVERT_TOOL: "🔁 " + _("格式互转"),
        NavKey.SCENE: "🎯 " + _("场景与模板"),
        NavKey.LOGIN_SESSION: "🔐 " + _("登录会话"),
        NavKey.QML_SHOWCASE: "🪟 " + _("市场橱窗（QML 试点）"),
        NavKey.GROUP_ADVANCED: _("高级"),
        NavKey.YAML_EDITOR: "📝 " + _("YAML 编辑器"),
        NavKey.EVIDENCE: "🔍 " + _("证据查看器"),
        NavKey.PLUGIN_MARKET: "🧩 " + _("OmniCrawler 市场"),
        NavKey.DEVELOPER: "🛠 " + _("开发者检查器"),
    }


def _invoke_run_or_trial(window: Any) -> None:
    canvas = getattr(window, "_task_canvas", None)
    trial_fn = getattr(window, "_request_trial_run", None)
    run_fn = getattr(window, "_request_run", None)
    if canvas is not None and callable(getattr(canvas, "trial_matches_fields", None)):
        if not canvas.trial_matches_fields() and callable(trial_fn):
            trial_fn()
            return
    if callable(run_fn):
        run_fn()
    elif callable(trial_fn):
        trial_fn()


def _invoke_stop(window: Any) -> None:
    delegate = getattr(window, "_run_delegate", None)
    stop_fn = getattr(delegate, "stop_task", None)
    if callable(stop_fn):
        stop_fn()


def _invoke_save(window: Any) -> None:
    delegate = getattr(window, "_config_delegate", None)
    save_fn = getattr(delegate, "save_config", None)
    if callable(save_fn):
        save_fn()


def _invoke_toggle_dnd(window: Any) -> None:
    theme_mgr = getattr(window, "_theme_manager", None)
    toggle_fn = getattr(theme_mgr, "toggle_dnd", None)
    if callable(toggle_fn):
        current = bool(getattr(window, "_dnd_mode", False))
        toggle_fn(not current)


def _invoke_toggle_theme(window: Any) -> None:
    settings = getattr(window, "_settings", None) or AppSettings.instance()
    current = str(getattr(settings, "theme", "light") or "light")
    target = "light" if current == "dark" else "dark"
    theme_mgr = getattr(window, "_theme_manager", None)
    set_theme = getattr(theme_mgr, "set_theme", None)
    if callable(set_theme):
        set_theme(target)
    else:
        settings.theme = target


def _invoke_help_center(window: Any) -> None:
    help_dock = getattr(window, "_help_center", None)
    if help_dock is not None and callable(getattr(help_dock, "show", None)):
        help_dock.show()
        raise_fn = getattr(help_dock, "raise_", None)
        if callable(raise_fn):
            raise_fn()
        return
    help_dialogs = getattr(window, "_help_dialogs", None)
    quick_start = getattr(help_dialogs, "show_quick_start", None)
    if callable(quick_start):
        quick_start()


def _navigate_to(window: Any, nav_key: str) -> None:
    """跳转到目标页面；若目标行在简单模式下被隐藏，先临时展开该行再切页。"""
    row_by_key = getattr(window, "_nav_row_by_key", None)
    nav = getattr(window, "_nav", None)
    if not isinstance(row_by_key, dict) or nav is None:
        return
    row = row_by_key.get(nav_key)
    if row is None:
        return
    item = nav.item(row)
    if item is not None and item.isHidden():
        item.setHidden(False)
    nav.setCurrentRow(row)


def _make_nav_callback(window: Any, nav_key: str) -> Callable[[], None]:
    return lambda: _navigate_to(window, nav_key)


def build_palette_commands(
    window: Any,
    *,
    capabilities: dict[str, bool] | None = None,
    nav_labels: Mapping[str, str] | None = None,
) -> list[PaletteCommand]:
    """构建能力感知的命令清单（可见导航页 + 全局动作）。"""
    caps = navigation_capabilities() if capabilities is None else capabilities
    labels = default_nav_labels() if nav_labels is None else nav_labels
    settings = getattr(window, "_settings", None) or AppSettings.instance()
    shortcuts: Mapping[str, str] = getattr(settings, "shortcuts", {})

    commands: list[PaletteCommand] = []
    current_group = _("页面导航")
    nav_prefix = _("导航")
    for entry in visible_entries(caps):
        label = str(labels.get(entry.key, entry.key))
        if entry.is_header:
            current_group = f"{nav_prefix} · {label}"
            continue
        key = entry.key
        commands.append(
            PaletteCommand(
                command_id=f"nav:{key}",
                title=label,
                category=current_group,
                keywords=_NAV_KEYWORDS.get(key, ()) + (key,),
                shortcut="",
                nav_key=key,
                callback=_make_nav_callback(window, key),
            )
        )

    action_category = _("全局动作")
    global_specs: tuple[tuple[str, str, tuple[str, ...], str, Callable[[], None]], ...] = (
        (
            "action:run",
            _("运行/试跑任务"),
            ("run", "start", "trial", "execute", "yunxing", "shipao"),
            str(shortcuts.get("run", "Ctrl+R")),
            lambda: _invoke_run_or_trial(window),
        ),
        (
            "action:stop",
            _("停止任务"),
            ("stop", "cancel", "abort", "tingzhi"),
            str(shortcuts.get("stop", "Ctrl+Shift+S")),
            lambda: _invoke_stop(window),
        ),
        (
            "action:save",
            _("保存配置"),
            ("save", "write", "config", "baocun"),
            str(shortcuts.get("save", "Ctrl+S")),
            lambda: _invoke_save(window),
        ),
        (
            "action:toggle_editor",
            _("切换工作台/YAML"),
            ("yaml", "editor", "workspace", "toggle", "switch", "qiehuan"),
            str(shortcuts.get("toggle_editor", "Ctrl+E")),
            lambda: _call_window_method(window, "_toggle_workspace_editor"),
        ),
        (
            "action:open_templates",
            _("打开模板库"),
            ("template", "library", "preset", "market", "mobanku"),
            str(shortcuts.get("open_templates", "Ctrl+T")),
            lambda: _call_window_method(window, "_show_template_library"),
        ),
        (
            "action:refresh",
            _("刷新结果页"),
            ("refresh", "reload", "result", "table", "shuaxin"),
            str(shortcuts.get("refresh", "F5")),
            lambda: _call_window_method(window, "_refresh_results_page"),
        ),
        (
            "action:format_yaml",
            _("格式化 YAML"),
            ("format", "pretty", "yaml", "lint", "geshihua"),
            str(shortcuts.get("format_yaml", "Ctrl+Shift+F")),
            lambda: _call_window_method(window, "_format_yaml_from_shortcut"),
        ),
        (
            "action:toggle_dnd",
            _("切换请勿打扰"),
            ("dnd", "mute", "quiet", "notify", "qingwudarao"),
            str(shortcuts.get("toggle_dnd", "Ctrl+Shift+M")),
            lambda: _invoke_toggle_dnd(window),
        ),
        (
            "action:toggle_theme",
            _("切换深色/浅色主题"),
            ("theme", "dark", "light", "appearance", "zhuti", "shense", "qianse"),
            "",
            lambda: _invoke_toggle_theme(window),
        ),
        (
            "action:help_center",
            _("打开帮助中心"),
            ("help", "docs", "guide", "faq", "bangzhu"),
            "",
            lambda: _invoke_help_center(window),
        ),
    )
    for cmd_id, title, kw, shortcut, fn in global_specs:
        commands.append(
            PaletteCommand(
                command_id=cmd_id,
                title=title,
                category=action_category,
                keywords=kw,
                shortcut=shortcut,
                nav_key=None,
                callback=fn,
            )
        )
    return commands


def _call_window_method(window: Any, attr_name: str) -> None:
    target = getattr(window, attr_name, None)
    if callable(target):
        target()


def _command_search_blob(cmd: PaletteCommand) -> str:
    parts = [
        cmd.title,
        cmd.category,
        cmd.command_id,
        cmd.shortcut,
        cmd.nav_key or "",
        " ".join(cmd.keywords),
    ]
    return " ".join(parts).casefold()


def filter_palette_commands(
    commands: Sequence[PaletteCommand], query: str
) -> list[PaletteCommand]:
    """多关键词子串 + 英文/拼音关键词过滤，前缀命中优先排序。"""
    tokens = [tok for tok in query.strip().casefold().split() if tok]
    if not tokens:
        return list(commands)

    scored: list[tuple[int, int, PaletteCommand]] = []
    for index, cmd in enumerate(commands):
        blob = _command_search_blob(cmd)
        if not all(tok in blob for tok in tokens):
            continue
        title_cf = cmd.title.casefold()
        kw_cf = tuple(k.casefold() for k in cmd.keywords)
        first = tokens[0]
        if first in kw_cf or title_cf == first:
            rank = 0
        elif any(k.startswith(first) for k in kw_cf) or first in title_cf:
            rank = 1
        else:
            rank = 2
        scored.append((rank, index, cmd))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in scored]


def format_palette_item_text(cmd: PaletteCommand) -> str:
    """格式化单行展示文本：主标题 · 分组徽标 + 右侧键帽快捷键提示。"""
    base = f"{cmd.title}    ·    {cmd.category}"
    if cmd.shortcut:
        return f"{base}      ⌨ {cmd.shortcut}"
    return base


class CommandPaletteDialog(QDialog):
    """Ctrl+K 全局命令面板与快速页面切换对话框（Raycast / Linear Spotlight 风格）。"""

    def __init__(
        self,
        window: Any,
        *,
        parent: QWidget | None = None,
        capabilities: dict[str, bool] | None = None,
        nav_labels: Mapping[str, str] | None = None,
    ) -> None:
        host_parent = parent if parent is not None else (window if isinstance(window, QWidget) else None)
        super().__init__(host_parent)
        self._window = window
        self.setObjectName("commandPaletteDialog")
        self.setAccessibleName(_("全局命令面板与快速跳转"))
        self.setWindowTitle(_("命令面板与快速跳转"))
        self.setModal(True)
        self.setMinimumWidth(580)
        self.resize(640, 448)

        self._all_commands: list[PaletteCommand] = build_palette_commands(
            window, capabilities=capabilities, nav_labels=nav_labels
        )
        self._filtered_commands: list[PaletteCommand] = []
        self.executed_command_id: str | None = None
        self._pending_callback: Callable[[], None] | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)
        layout.setSpacing(10)

        search_frame = QFrame(self)
        search_frame.setObjectName("commandPaletteSearchFrame")
        search_frame.setAccessibleName(_("命令面板搜索栏"))
        search_layout = QHBoxLayout(search_frame)
        search_layout.setContentsMargins(12, 4, 12, 4)
        search_layout.setSpacing(8)

        search_badge = QLabel("⌘K", search_frame)
        search_badge.setObjectName("commandPaletteBadge")
        search_badge.setAccessibleName(_("命令面板快捷键标识"))
        search_layout.addWidget(search_badge)

        self.search_input = QLineEdit(search_frame)
        self.search_input.setObjectName("commandPaletteInput")
        self.search_input.setAccessibleName(_("搜索页面或全局命令"))
        self.search_input.setPlaceholderText(
            _("输入页面名称、英文或拼音关键词（如 yaml / market / result / run / theme）…")
        )
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._refresh_list)
        self.search_input.installEventFilter(self)
        search_layout.addWidget(self.search_input, 1)

        esc_keycap = QLabel("ESC", search_frame)
        esc_keycap.setObjectName("commandPaletteKeycap")
        esc_keycap.setAccessibleName(_("按 Esc 关闭"))
        search_layout.addWidget(esc_keycap)
        layout.addWidget(search_frame)

        self.result_list = QListWidget(self)
        self.result_list.setObjectName("commandPaletteList")
        self.result_list.setAccessibleName(_("匹配的页面与命令列表"))
        self.result_list.setUniformItemSizes(True)
        self.result_list.itemActivated.connect(self._on_item_activated)
        layout.addWidget(self.result_list, 1)

        footer_frame = QFrame(self)
        footer_frame.setObjectName("commandPaletteFooter")
        footer_frame.setAccessibleName(_("命令面板底栏"))
        footer = QHBoxLayout(footer_frame)
        footer.setContentsMargins(10, 6, 10, 6)
        footer.setSpacing(8)
        self.hint_label = QLabel(_("↑↓ 选择 · Enter 执行 · Esc 关闭"), footer_frame)
        self.hint_label.setObjectName("muted")
        self.hint_label.setAccessibleName(_("命令面板操作提示"))
        footer.addWidget(self.hint_label)
        footer.addStretch()

        self.execute_button = QPushButton(_("执行"), footer_frame)
        self.execute_button.setAccessibleName(_("执行当前选中的命令"))
        self.execute_button.setProperty("primary", True)
        self.execute_button.clicked.connect(self.execute_selected)
        footer.addWidget(self.execute_button)

        self.close_button = QPushButton(_("关闭"), footer_frame)
        self.close_button.setAccessibleName(_("关闭命令面板"))
        self.close_button.clicked.connect(self.reject)
        footer.addWidget(self.close_button)
        layout.addWidget(footer_frame)

        self._refresh_list("")
        self.search_input.setFocus()

    @property
    def filtered_commands(self) -> list[PaletteCommand]:
        """当前过滤结果（只读副本）。"""
        return list(self._filtered_commands)

    def _refresh_list(self, query: str) -> None:
        self._filtered_commands = filter_palette_commands(self._all_commands, query)
        self.result_list.clear()
        if not self._filtered_commands:
            empty = QListWidgetItem(_("未找到匹配的页面或命令"))
            empty.setFlags(Qt.ItemFlag.NoItemFlags)
            self.result_list.addItem(empty)
            self.execute_button.setEnabled(False)
            self.hint_label.setText(_("无匹配项 · 清空关键词可查看全部可用入口"))
            return

        for cmd in self._filtered_commands:
            item = QListWidgetItem(format_palette_item_text(cmd))
            item.setData(Qt.ItemDataRole.UserRole, cmd.command_id)
            tooltip = f"{cmd.title} ({cmd.category})"
            if cmd.shortcut:
                tooltip = f"{tooltip} — {cmd.shortcut}"
            item.setToolTip(tooltip)
            self.result_list.addItem(item)

        self.result_list.setCurrentRow(0)
        self.execute_button.setEnabled(True)
        self.hint_label.setText(
            _("共 {0} 项匹配 · ↑↓ 选择 · Enter 执行 · Esc 关闭").format(
                len(self._filtered_commands)
            )
        )

    def move_selection(self, delta: int) -> None:
        """按偏移量移动当前选中行（支持键盘上下循环或边界停留）。"""
        total = len(self._filtered_commands)
        if total == 0:
            return
        current = self.result_list.currentRow()
        if current < 0:
            next_row = 0
        else:
            next_row = (current + delta) % total
        self.result_list.setCurrentRow(next_row)

    def execute_selected(self) -> bool:
        """执行当前选中的命令并关闭对话框，返回是否成功触发。"""
        row = self.result_list.currentRow()
        if row < 0 or row >= len(self._filtered_commands):
            return False
        cmd = self._filtered_commands[row]
        self.executed_command_id = cmd.command_id
        self._pending_callback = cmd.callback
        self.accept()
        callback = self._pending_callback
        self._pending_callback = None
        if callback is not None:
            callback()
        return True

    def _on_item_activated(self, _item: QListWidgetItem) -> None:
        self.execute_selected()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt QObject 虚函数固定签名
        if watched is self.search_input and event.type() == QEvent.Type.KeyPress:
            assert isinstance(event, QKeyEvent)
            key = event.key()
            if key == Qt.Key.Key_Down:
                self.move_selection(1)
                return True
            if key == Qt.Key.Key_Up:
                self.move_selection(-1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.execute_selected()
                return True
            if key == Qt.Key.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(watched, event)
