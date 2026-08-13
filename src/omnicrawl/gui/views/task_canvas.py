"""任务画布（Task Canvas）— P0 画布骨架。

五区域渐进布局：意图区 → 草稿区 → 字段区 → 验证区 → 交付区。
全手动轨全流程，含 P0 硬约束：
- 运行唯一出口（交付区无运行按钮，试跑通过后验证区才出现「保存并全量运行」）
- 字段/草稿变更 → 试跑状态失效（stale 警告条 + 运行按钮禁用）
- 锁定态（外部 YAML 编辑冲突）禁保存、禁编辑
- 「保存草稿」不清除脏标记（脏标记是内存编辑态与回写冲突的控制器）

外部接口（供 main.py 接线）：
- 信号：config_changed / save_requested / trial_run_requested / run_requested / yaml_view_requested
- 方法：load_config / apply_draft / set_trial_result / notify_external_edit /
         set_locked / restart / focus_url_input / set_simple_mode / get_config
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, QSettings, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStyle,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..core.config_model import CrawlConfig, FieldDef
from ..design_system import FONT_SIZE, RADIUS, SPACING, ThemeManager
from ..i18n import _
from ..widgets.help_tooltip import HelpTooltip
from ..widgets.toast import ToastManager

# 采集方式简写（草稿摘要用）
_SOURCE_KIND_SHORT = {
    "static_html": _("静态网页"),
    "crawl": _("栏目发现"),
    "browser": _("动态浏览器"),
    "rest": _("REST API"),
    "feed": _("RSS/Feed"),
    "focused": _("定向采集"),
}

_OUTPUT_FORMATS = [
    ("jsonl", _("JSONL")),
    ("csv", _("CSV")),
    ("xlsx", _("Excel")),
]

_TRIAL_PAGES_DEFAULT = 3
_TRIAL_PAGES_MIN = 1
_TRIAL_PAGES_MAX = 10

# P1：本地启发式补全的通用字段规则（无 AI 轨的兜底来源，绝不覆盖用户字段）
_GENERIC_FIELD_RULES: tuple[FieldDef, ...] = (
    FieldDef(name=_("标题"), selector="h1", selector_type="css"),
    FieldDef(name=_("链接"), selector="a", selector_type="css"),
    FieldDef(name=_("日期"), selector="time", selector_type="css"),
    FieldDef(name=_("作者"), selector=".author", selector_type="css"),
    FieldDef(name=_("摘要"), selector=".description", selector_type="css"),
)


def _repolish_widget(widget: QWidget) -> None:
    """按 QSS 动态属性刷新控件外观。"""
    style = widget.style()
    if isinstance(style, QStyle):
        style.unpolish(widget)
        style.polish(widget)
    widget.ensurePolished()


def _selector_kind(selector: str) -> str:
    """判断选择器是 XPath 还是 CSS（默认 css）；与 step3_fields.selector_kind 同语义。"""
    stripped = (selector or "").strip()
    if not stripped:
        return "css"
    # XPath 通常以 / .// ( @ [ 或 // 开头；CSS 选择器不会
    if stripped.startswith(("/", ".//", "(", "@", "//")) or "[@" in stripped:
        return "xpath"
    return "css"


class _PlanReviewWorker(QThread):
    """P4：后台生成 AI 任务计划（复用 natural_language_task.compile_with_ai）。

    与 home.py _AIEnrichWorker 同模式：隐私闸门 → provider 单一真源 → 失败/未启用
    显式区分信号。审核动作本身与 AI 解耦——本 worker 只负责产出「AI 计划」内容，
    采纳/忽略由画布纯 UI 处理，无 AI 时画布走本地轨不受影响。
    """

    result_ready = pyqtSignal(object)  # NaturalLanguageDraft（ai_enhanced=True）
    ai_unavailable = pyqtSignal(str)  # reason（未启用/隐私禁用）
    ai_error = pyqtSignal(str)  # error message（调用失败/越权拦截）

    def __init__(self, request: str, parent: QWidget | None = None, project_root: str | None = None) -> None:
        super().__init__(parent)
        self._request = request
        self._project_root = project_root

    def run(self) -> None:
        try:
            # 隐私闸门：页面文本外发被禁用时直接跳过 AI，不静默发走
            from ...core.ai_env import load_ai_privacy

            privacy = load_ai_privacy(self._project_root)
            if not privacy.get("allow_page_text", True):
                self.ai_unavailable.emit(_("AI 页面文本外发已按隐私设置禁用，已使用本地解析"))
                return

            from ...services.natural_language_task import compile_with_ai

            provider = self._load_provider()
            if provider is None:
                self.ai_unavailable.emit(_("AI 未启用：请在「AI 服务中心」配置后重试"))
                return

            result = compile_with_ai(self._request, provider)
            self.result_ready.emit(result)
        except Exception as exc:  # noqa: BLE001 - 错误需上抛给 UI，不再是静默 None
            reason = str(exc).strip() or type(exc).__name__
            self.ai_error.emit(reason)
        finally:
            self.deleteLater()

    def _load_provider(self) -> object | None:
        """从单一真源构造 AI provider（含 Egress 审计；未启用返回 None）。"""
        from ...services.ai_providers import provider_from_env

        return provider_from_env(project_root=self._project_root)


class _FieldTableModel(QAbstractTableModel):
    """字段表格 model（PRD §3.3：QAbstractTableModel + QTableView 虚拟滚动内建）。

    渐进披露：数据就绪后默认只显示前 ``_INITIAL_VISIBLE_ROWS`` 行，
    「还有 N 个字段，点击加载」触发 ``show_all()`` 展开全部——
    50 字段场景首屏只绘制可见行，满足数据就绪 → 首屏 <500ms 与滚动 ≥30fps 基线。
    """

    _HEADERS: tuple[str, ...] = (_("名称"), _("选择器"), _("类型"))
    _INITIAL_VISIBLE_ROWS = 10

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._fields: list[FieldDef] = []
        self._visible: int | None = self._INITIAL_VISIBLE_ROWS

    # ── Qt model API ────────────────────────────────────
    def rowCount(self, parent: QModelIndex | None = None) -> int:
        if parent is not None and parent.isValid():
            return 0
        total = len(self._fields)
        if self._visible is None:
            return total
        return min(total, self._visible)

    def columnCount(self, parent: QModelIndex | None = None) -> int:
        return 3

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._fields):
            return None
        if role not in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return None
        field = self._fields[index.row()]
        return (field.name, field.selector, field.selector_type)[index.column()]

    def headerData(self, section: int, orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self._HEADERS[section] if section < len(self._HEADERS) else None
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        return (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsEditable
        )

    def setData(self, index: QModelIndex, value: Any, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if not index.isValid() or role != Qt.ItemDataRole.EditRole:
            return False
        field = self._fields[index.row()]
        text = str(value)
        if index.column() == 0:
            if not text.strip():
                return False
            field.name = text.strip()
        elif index.column() == 1:
            field.selector = text
        else:
            field.selector_type = text if text in ("css", "xpath", "jsonpath") else "css"
        self.dataChanged.emit(index, index, [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole])
        return True

    # ── 画布专用 API ────────────────────────────────────
    def set_fields(self, fields: list[FieldDef]) -> None:
        """数据就绪后整体替换（一次性 layoutChanged，首屏只绘制可见行）。"""
        self._fields = list(fields)
        self._visible = self._INITIAL_VISIBLE_ROWS
        self.layoutChanged.emit()

    def rows(self) -> list[FieldDef]:
        return list(self._fields)

    def append(self, field: FieldDef) -> None:
        row = len(self._fields)
        self.beginInsertRows(QModelIndex(), row, row)
        self._fields.append(field)
        self.endInsertRows()

    def remove_row(self, row: int) -> None:
        if 0 <= row < len(self._fields):
            self.beginRemoveRows(QModelIndex(), row, row)
            del self._fields[row]
            self.endRemoveRows()

    def show_all(self) -> None:
        if self._visible is None:
            return
        self._visible = None
        self.layoutChanged.emit()

    def hidden_count(self) -> int:
        if self._visible is None:
            return 0
        return max(0, len(self._fields) - self._visible)

    def field_names(self) -> set[str]:
        return {f.name for f in self._fields if f.name.strip()}


class _Section(QGroupBox):
    """可折叠区域容器：标题 + 折叠按钮 + 内容。

    ``sticky`` 为折叠时仍常驻显示的状态条（如验证区试跑状态栏，
    满足 PRD §2.4「验证区永不消失」）；其余 body 内容折叠时隐藏。
    """

    toggled = pyqtSignal(bool)

    def __init__(
        self,
        title: str,
        parent: QWidget | None = None,
        *,
        sticky: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._collapsed = False
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        self._title_label = QLabel(title)
        self._title_label.setObjectName("sectionTitle")
        header.addWidget(self._title_label)
        header.addStretch()
        self._fold_btn = QPushButton(_("收起"))
        self._fold_btn.setObjectName("foldBtn")
        self._fold_btn.setFlat(True)
        self._fold_btn.clicked.connect(self._toggle)
        header.addWidget(self._fold_btn)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SPACING["lg"], SPACING["sm"], SPACING["lg"], SPACING["md"])
        outer.addLayout(header)
        self._body_host = QWidget()
        self._body = QVBoxLayout(self._body_host)
        self._body.setSpacing(SPACING["md"])
        outer.addWidget(self._body_host)
        self._sticky_widget = sticky
        if sticky is not None:
            outer.addWidget(sticky)

    def body(self) -> QVBoxLayout:
        return self._body

    def _toggle(self) -> None:
        self._collapsed = not self._collapsed
        self._fold_btn.setText(_("展开") if self._collapsed else _("收起"))
        # 折叠只隐藏 body 内容；sticky 状态条保持常驻
        self._body_host.setVisible(not self._collapsed)
        self.toggled.emit(self._collapsed)
        _repolish_widget(self)

    def collapsed(self) -> bool:
        return self._collapsed


class TaskCanvas(QScrollArea):
    """五区域任务画布。"""

    config_changed = pyqtSignal()
    save_requested = pyqtSignal()
    trial_run_requested = pyqtSignal()
    run_requested = pyqtSignal()
    yaml_view_requested = pyqtSignal()
    # P2：意图区 URL 探活（600ms 停顿后触发；结果经 set_probe_result 回填徽标）
    probe_requested = pyqtSignal(str)

    # 探活停顿（毫秒）：用户停止输入后再发起轻量探测
    _PROBE_DEBOUNCE_MS = 600
    # P3：首启引导气泡时长（毫秒），3 秒自动消失（PRD §3.1）
    _WELCOME_TIP_MS = 3000
    _WELCOME_TIP_KEY = "canvas/welcome_tip_seen"

    def __init__(
        self,
        config: CrawlConfig,
        parent: QWidget | None = None,
        *,
        project_root: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._config = config
        self._project_root = project_root
        self._updating = False
        # 按域脏标记（PRD §2.2.1）：scope=URL/范围/预算，field=字段规则，
        # output=输出格式/存储，schedule=调度/监测。AI 回写只受其对应域约束。
        self._dirty_domains: set[str] = set()
        self._locked = False
        self._trial_ok = False
        self._trial_field_hash: str | None = None
        # P2：试跑历史（最近 3 次，PRD §3.4）
        self._trial_history: list[dict[str, Any]] = []
        self._recommendation: Any | None = None
        self._simple_mode = False
        # P2：URL 探活防抖计时器（single-shot，仅调度不联网；实际请求由 main 侧 worker 执行）
        self._probe_timer = QTimer(self)
        self._probe_timer.setSingleShot(True)
        self._probe_timer.timeout.connect(self._fire_probe)
        # P3：首启引导气泡（PRD §3.1）——3 秒自动消失，非弹窗不阻塞
        self._welcome_timer = QTimer(self)
        self._welcome_timer.setSingleShot(True)
        self._welcome_timer.timeout.connect(self._dismiss_welcome_tip)
        # P4：AI 计划审核（后台 worker 产出；采纳/忽略与 AI 解耦）
        self._plan_worker: _PlanReviewWorker | None = None
        self._ai_plan_draft: Any | None = None
        # 测试钩子：置 False 可整体停用 AI 计划后台线程（无 AI 时本就不触发）
        self._ai_plan_enabled = True

        self.setWidgetResizable(True)
        self.setObjectName("taskCanvas")
        root = QWidget()
        root.setObjectName("taskCanvasRoot")
        # 持有 root 的 Python 引用，防止无 parent 顶层控件被 GC 连带删除子树
        # （PyQt 下仅靠 C++ parent 无法阻止 Python wrapper 回收）。
        self._root_widget = root
        self.setWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(SPACING["xl"], SPACING["lg"], SPACING["xl"], SPACING["xl"])
        layout.setSpacing(SPACING["lg"])

        layout.addLayout(self._build_toolbar())

        # ① 意图区（常驻）
        self._intent_section = _Section(_("① 从网址或描述开始"))
        self._build_intent_area()
        layout.addWidget(self._intent_section)

        # ② 草稿区
        self._draft_section = _Section(_("② 任务草稿"))
        self._build_draft_area()
        layout.addWidget(self._draft_section)

        # ③ 字段区
        self._fields_section = _Section(_("③ 字段（复核式）"))
        self._build_fields_area()
        layout.addWidget(self._fields_section)

        # ④ 验证区（sticky：折叠时保留底部状态栏，PRD §2.4 验证区永不消失）
        self._trial_section = _Section(_("④ 验证与运行"), sticky=self._build_trial_statusbar())
        self._build_trial_area()
        layout.addWidget(self._trial_section)

        # ⑤ 交付区
        self._delivery_section = _Section(_("⑤ 交付"))
        self._build_delivery_area()
        layout.addWidget(self._delivery_section)

        layout.addStretch()

        # 初始折叠态：草稿/字段/验证/交付在未生成前收起
        for section in (self._draft_section, self._fields_section, self._trial_section, self._delivery_section):
            self._collapse_section(section, True)

        ThemeManager.instance().theme_changed.connect(self._apply_style)
        self._apply_style()
        self._sync_ui_state()

    # ------------------------------------------------------------------
    #  工具条
    # ------------------------------------------------------------------
    def _build_toolbar(self) -> QHBoxLayout:
        bar = QHBoxLayout()
        bar.addWidget(HelpTooltip("task.name"))
        title = QLabel(_("任务画布"))
        title.setObjectName("pageTitle")
        bar.addWidget(title)
        bar.addStretch()
        self._yaml_btn = QPushButton(_("查看 YAML"))
        self._yaml_btn.setObjectName("secondary")
        self._yaml_btn.setToolTip(_("只读源码视图；编辑请用侧栏 YAML 编辑器"))
        self._yaml_btn.clicked.connect(self.yaml_view_requested)
        bar.addWidget(self._yaml_btn)
        # P3：长尾「?」按需查阅（HelpTooltip：悬停摘要 + 点击打开帮助中心 yaml.editor 条目）
        bar.addWidget(HelpTooltip("yaml.editor"))
        self._save_btn = QPushButton(_("保存草稿"))
        self._save_btn.setProperty("primary", True)
        self._save_btn.setToolTip(_("随时可保存，无需先试跑；不改变编辑状态"))
        self._save_btn.clicked.connect(self.save_requested)
        bar.addWidget(self._save_btn)
        return bar

    # ------------------------------------------------------------------
    #  ① 意图区
    # ------------------------------------------------------------------
    def _build_intent_area(self) -> None:
        body = self._intent_section.body()
        # P3：首启引导气泡（默认隐藏；maybe_show_welcome_tip 触发）
        self._welcome_tip = QLabel("")
        self._welcome_tip.setObjectName("welcomeTip")
        self._welcome_tip.setVisible(False)
        body.addWidget(self._welcome_tip)

        url_row = QHBoxLayout()
        url_row.addWidget(HelpTooltip("source.seed"))
        self._url_edit = QLineEdit()
        self._url_edit.setPlaceholderText(_("试试粘贴一个网址开始，例如 https://example.org/news"))
        self._url_edit.textChanged.connect(self._on_intent_changed)
        url_row.addWidget(self._url_edit)
        body.addLayout(url_row)

        desc_row = QHBoxLayout()
        desc_row.addWidget(HelpTooltip("task.intent"))
        desc_row.addWidget(HelpTooltip("ai.mode"))
        self._desc_edit = QLineEdit()
        self._desc_edit.setPlaceholderText(_("（可选）一句话描述任务，如：采集新闻标题并监测变化"))
        self._desc_edit.setClearButtonEnabled(True)
        self._desc_edit.textChanged.connect(self._on_intent_changed)
        desc_row.addWidget(self._desc_edit)
        body.addLayout(desc_row)

        # P2：URL 探活反馈行（可访问性 / 静态或动态 / 分页线索；失败静默降级）
        probe_row = QHBoxLayout()
        self._probe_badge = QLabel("")
        self._probe_badge.setObjectName("muted")
        self._probe_badge.setWordWrap(True)
        probe_row.addWidget(self._probe_badge)
        probe_row.addStretch()
        body.addLayout(probe_row)

        row = QHBoxLayout()
        self._url_badge = QLabel("")
        self._url_badge.setObjectName("badge")
        row.addWidget(self._url_badge)
        row.addStretch()
        self._start_btn = QPushButton(_("开始"))
        self._start_btn.setProperty("primary", True)
        self._start_btn.setEnabled(False)
        self._start_btn.setToolTip(_("需要有效网址"))
        self._start_btn.clicked.connect(self._on_start)
        row.addWidget(self._start_btn)
        body.addLayout(row)

    def _on_intent_changed(self) -> None:
        url = self._url_edit.text().strip()
        valid = bool(url) and self._is_valid_url(url)
        # P3：用户开始输入即提前关闭首启气泡（不打扰）
        if url:
            self._dismiss_welcome_tip()
        self._start_btn.setEnabled(valid and not self._locked)
        self._start_btn.setToolTip(_("需要有效网址") if not valid else _("根据网址生成任务草稿"))
        self._url_badge.setText("")
        if url:
            self._url_badge.setText(_("✓ 已识别网址") if valid else _("⚠ 网址格式不完整"))
            _repolish_widget(self._url_badge)
        # P2：URL 有效且未锁定时调度探活（600ms 防抖）；否则取消挂起探测并清空反馈
        if valid and not self._locked:
            self._probe_timer.start(self._PROBE_DEBOUNCE_MS)
        else:
            self._probe_timer.stop()
            self._set_probe_badge("")
        self._on_scope_changed()

    def _fire_probe(self) -> None:
        """防抖期满：意图区当前 URL 仍有效才发起探活（锁定态/输入变更时不发）。"""
        url = self._url_edit.text().strip()
        if self._locked or not url or not self._is_valid_url(url):
            return
        self._set_probe_badge(_("正在探测…"))
        self.probe_requested.emit(url)

    def set_probe_result(self, url: str, report: dict | None) -> None:
        """探活结果回填徽标；URL 已变更则视为过期结果，丢弃不显示。"""
        if url != self._url_edit.text().strip() or report is None:
            return
        page_type = {
            "list": _("列表页"),
            "detail": _("详情页"),
            "search": _("搜索页"),
            "unknown": _("结构未知"),
        }.get(str(report.get("page_type", "unknown")), _("结构未知"))
        kind = _("动态页面") if report.get("dynamic") else _("静态页面")
        pagination = bool(report.get("pagination"))
        pagination_text = _("含分页线索") if pagination else _("无分页线索")
        self._set_probe_badge(
            _("✓ 可访问 · %(page)s · %(kind)s · %(pagination)s") % {
                "page": page_type, "kind": kind, "pagination": pagination_text,
            }
        )

    def set_probe_failed(self, url: str, _message: str) -> None:
        """探活失败静默降级：仅提示可手动配置，绝不阻断主流程。"""
        if url != self._url_edit.text().strip():
            return
        self._set_probe_badge(_("探测不可用（网络受限或站点拒绝自动访问）；可继续手动配置"))

    def _set_probe_badge(self, text: str) -> None:
        self._probe_badge.setText(text)
        _repolish_widget(self._probe_badge)

    # ------------------------------------------------------------------
    #  P3：首启引导气泡（PRD §3.1）——最克制：非弹窗、3 秒消失、不重复
    # ------------------------------------------------------------------
    def maybe_show_welcome_tip(self) -> None:
        """首次打开画布且无草稿时，输入框短暂高亮 + 气泡提示（3 秒自动消失）。

        已看过（本地偏好）或画布已有草稿则跳过；关闭后不再重复。
        """
        if self._config.seed_urls:
            return
        settings = QSettings("OmniCrawler", "GUIWorkbench")
        if settings.value(self._WELCOME_TIP_KEY, False, type=bool):
            return
        self._welcome_tip.setText(_("💡 试试粘贴一个网址开始"))
        self._welcome_tip.setVisible(True)
        self._url_edit.setProperty("welcomeHighlight", True)
        _repolish_widget(self._url_edit)
        self._welcome_timer.start(self._WELCOME_TIP_MS)

    def _dismiss_welcome_tip(self) -> None:
        """关闭气泡 + 取消高亮 + 记录本地偏好（不再重复）。"""
        self._welcome_timer.stop()
        self._welcome_tip.setVisible(False)
        if self._url_edit.property("welcomeHighlight"):
            self._url_edit.setProperty("welcomeHighlight", False)
            _repolish_widget(self._url_edit)
        settings = QSettings("OmniCrawler", "GUIWorkbench")
        settings.setValue(self._WELCOME_TIP_KEY, True)

    def _on_start(self) -> None:
        url = self._url_edit.text().strip()
        if not self._is_valid_url(url):
            self._url_badge.setText(_("⚠ 网址格式不完整"))
            return
        from ...services.ux_service import draft_quick_task

        intent = _intent_from_text(self._desc_edit.text())
        try:
            draft = draft_quick_task(url, intent)
        except ValueError as exc:
            ToastManager.instance().warning(str(exc))
            return
        self.apply_draft(draft)
        self._clear_dirty()  # 草稿生成视为确认态
        self._sync_ui_state()
        # P1：草稿生成后本地推断模板推荐（L1/L2，零网络）
        self._refresh_recommendation()
        # N4：草稿生成后懒加载场景列表（选用场景 → 槽位字段 + 基因增强）
        self._refresh_scenes()
        ToastManager.instance().success(_("已生成任务草稿：请复核后试跑"))
        # P4：有描述时后台生成 AI 计划供审核（未启用/失败不影响本地轨）
        self._start_plan_review()

    # ------------------------------------------------------------------
    #  P4：AI 计划审核卡片（PRD §5：审核动作与 AI 解耦）
    # ------------------------------------------------------------------
    def _request_text(self) -> str:
        """意图区完整请求文本（URL + 一句话描述），供 AI 计划解析。"""
        url = self._url_edit.text().strip()
        desc = self._desc_edit.text().strip()
        return (" ".join(part for part in (url, desc) if part)).strip()

    def _start_plan_review(self) -> None:
        """后台生成 AI 计划；请求为空、已禁用或已有 worker 在跑则跳过。"""
        request = self._request_text()
        if not self._ai_plan_enabled or not request or self._plan_worker is not None:
            return
        worker = _PlanReviewWorker(request, self, project_root=self._project_root)
        worker.result_ready.connect(self._on_ai_plan_ready)
        worker.ai_unavailable.connect(self._on_ai_plan_unavailable)
        worker.ai_error.connect(self._on_ai_plan_error)
        self._plan_worker = worker
        worker.start()

    def _on_ai_plan_ready(self, draft: Any) -> None:
        """AI 计划返回：展示审核卡片；仅接受当前 worker 的结果（restart 后旧结果丢弃）。"""
        if self.sender() is not self._plan_worker:
            return
        self._plan_worker = None
        if draft is None or not getattr(draft, "ai_enhanced", False):
            return
        self._ai_plan_draft = draft
        task = draft.task
        url = getattr(task, "url", "") or ""
        intent = str(getattr(task, "intent", "") or "")
        kind = _SOURCE_KIND_SHORT.get(str(getattr(task, "source_kind", "")), _("定向采集"))
        topics = "、".join(getattr(draft, "topics", ())) or _("未指定主题")
        lines = [
            f"<b>{_('入口')}</b>　{url or _('沿用当前网址')}",
            f"<b>{_('采集方式')}</b>　{kind}　<small>{_('意图')}：{intent}</small>",
            f"<b>{_('主题')}</b>　{topics}",
        ]
        for assumption in getattr(draft, "ai_assumptions", ())[:3]:
            field = assumption.get("field", "")
            value = assumption.get("value", "")
            reason = assumption.get("reason", "")
            lines.append(f"<small>· {_('假设')} {field}={value}（{reason}）</small>")
        for rec in getattr(draft, "ai_recommendations", ())[:3]:
            lines.append(f"<small>· {_('建议')} {rec}</small>")
        self._plan_title.setText(_("🤖 AI 计划已生成"))
        self._plan_source_label.setText(_("AI 生成 · 建议仅供参考"))
        self._plan_text.setText("<br>".join(lines))
        self._plan_card.setVisible(True)
        self._collapse_section(self._draft_section, False)
        ToastManager.instance().info(_("AI 计划已生成：可采纳或忽略"))

    def _on_ai_plan_unavailable(self, reason: str) -> None:
        """AI 未启用/隐私禁用：不打扰，画布继续走本地轨（审核动作与 AI 解耦）。"""
        if self.sender() is not self._plan_worker:
            return
        self._plan_worker = None
        LOGGER.debug("AI plan unavailable: %s", reason)

    def _on_ai_plan_error(self, message: str) -> None:
        """AI 计划生成失败：静默降级，本地草稿不受影响。"""
        if self.sender() is not self._plan_worker:
            return
        self._plan_worker = None
        LOGGER.debug("AI plan review failed: %s", message)

    def _accept_plan(self) -> None:
        """采纳 AI 计划：应用 AI 草稿（随后照常复核字段并试跑，不绕过任何护栏）。"""
        if self._ai_plan_draft is None:
            return
        draft = self._ai_plan_draft
        self._ai_plan_draft = None
        self._plan_card.setVisible(False)
        self.apply_draft(draft.task)
        self._clear_dirty()
        self._sync_ui_state()
        self._refresh_recommendation()
        self._collapse_section(self._fields_section, False)
        ToastManager.instance().success(_("已采纳 AI 计划，请复核字段后试跑"))

    def _dismiss_plan(self) -> None:
        """忽略 AI 计划：保留本地草稿，仅收起卡片。"""
        self._ai_plan_draft = None
        self._plan_card.setVisible(False)

    # ------------------------------------------------------------------
    #  ② 草稿区
    # ------------------------------------------------------------------
    def _build_draft_area(self) -> None:
        body = self._draft_section.body()
        # P4：AI 计划审核卡片（默认隐藏；仅 AI 计划生成后出现，采纳/忽略不依赖 AI）
        self._plan_card = QFrame()
        self._plan_card.setObjectName("planCard")
        self._plan_card.setVisible(False)
        plan_layout = QVBoxLayout(self._plan_card)
        plan_layout.setContentsMargins(SPACING["md"], SPACING["sm"], SPACING["md"], SPACING["sm"])
        plan_layout.setSpacing(SPACING["xs"])
        plan_title_row = QHBoxLayout()
        self._plan_title = QLabel("")
        self._plan_title.setObjectName("planCardTitle")
        plan_title_row.addWidget(self._plan_title)
        plan_title_row.addStretch()
        self._plan_source_label = QLabel("")
        self._plan_source_label.setObjectName("muted")
        plan_title_row.addWidget(self._plan_source_label)
        plan_layout.addLayout(plan_title_row)
        self._plan_text = QLabel("")
        self._plan_text.setWordWrap(True)
        self._plan_text.setObjectName("muted")
        plan_layout.addWidget(self._plan_text)
        plan_actions = QHBoxLayout()
        self._accept_plan_btn = QPushButton(_("采纳计划"))
        self._accept_plan_btn.setProperty("primary", True)
        self._accept_plan_btn.setToolTip(_("把 AI 计划应用到画布，随后照常复核字段并试跑"))
        self._accept_plan_btn.clicked.connect(self._accept_plan)
        plan_actions.addWidget(self._accept_plan_btn)
        self._dismiss_plan_btn = QPushButton(_("忽略"))
        self._dismiss_plan_btn.setFlat(True)
        self._dismiss_plan_btn.setToolTip(_("忽略 AI 计划，保留当前本地草稿"))
        self._dismiss_plan_btn.clicked.connect(self._dismiss_plan)
        plan_actions.addWidget(self._dismiss_plan_btn)
        plan_actions.addStretch()
        plan_layout.addLayout(plan_actions)
        body.addWidget(self._plan_card)

        badge_row = QHBoxLayout()
        badge_row.addWidget(HelpTooltip("source.kind"))
        self._source_badge = QLabel("")
        self._source_badge.setObjectName("badge")
        badge_row.addWidget(self._source_badge)
        badge_row.addStretch()
        body.addLayout(badge_row)

        # P1：模板推荐行（本地 L1/L2，零网络）——来源徽标旁给出「将按 XX 模板配置」
        self._rec_label = QLabel("")
        self._rec_label.setObjectName("muted")
        self._rec_label.setWordWrap(True)
        body.addWidget(self._rec_label)

        rec_row = QHBoxLayout()
        self._template_combo = QComboBox()
        self._template_combo.setEnabled(False)
        self._template_combo.setToolTip(_("空=用自动推荐；选中=强制使用该模板（写入 YAML）"))
        self._template_combo.currentIndexChanged.connect(self._on_template_override_changed)
        rec_row.addWidget(self._template_combo)
        # N4：选用场景（懒加载 SceneStore）——槽位定义生成字段 + 写 extract.scene
        self._scene_combo = QComboBox()
        self._scene_combo.setEnabled(False)
        self._scene_combo.setToolTip(_("选用已验收场景：按槽位生成字段并启用基因增强"))
        self._scene_combo.currentIndexChanged.connect(self._on_scene_changed)
        rec_row.addWidget(self._scene_combo)
        self._ignore_rec_btn = QPushButton(_("忽略推荐"))
        self._ignore_rec_btn.setFlat(True)
        self._ignore_rec_btn.setEnabled(False)
        self._ignore_rec_btn.setToolTip(_("忽略模板推荐，按当前草稿手动配置"))
        self._ignore_rec_btn.clicked.connect(self._ignore_recommendation)
        rec_row.addWidget(self._ignore_rec_btn)
        # P2：拒绝理由采集（PRD §3.2）——预设标签 + 诊断快照，无快照不入库
        self._reject_btn = QPushButton(_("👎 不准确"))
        self._reject_btn.setFlat(True)
        self._reject_btn.setEnabled(False)
        self._reject_btn.setToolTip(_("推荐不准确？记录原因，帮助改进后续推荐"))
        self._reject_btn.clicked.connect(self._collect_rejection)
        rec_row.addWidget(self._reject_btn)
        rec_row.addStretch()
        body.addLayout(rec_row)

        self._summary_label = QLabel("")
        self._summary_label.setObjectName("summaryText")
        self._summary_label.setWordWrap(True)
        self._summary_label.setTextFormat(Qt.TextFormat.RichText)
        body.addWidget(self._summary_label)

        self._advanced_btn = QPushButton(_("高级设置 ▶"))
        self._advanced_btn.setFlat(True)
        self._advanced_btn.clicked.connect(self._toggle_advanced)
        body.addWidget(self._advanced_btn)

        self._advanced_box = QFrame()
        self._advanced_box.setObjectName("advancedBox")
        adv = QVBoxLayout(self._advanced_box)
        adv.setContentsMargins(SPACING["md"], SPACING["sm"], SPACING["md"], SPACING["sm"])
        adv.setSpacing(SPACING["sm"])
        self._max_pages = QSpinBox()
        pages_row = QHBoxLayout()
        pages_row.addWidget(HelpTooltip("crawl.max_pages"))
        pages_row.addWidget(HelpTooltip("source.pagination"))
        pages_label = QLabel(_("最大页数"))
        pages_label.setObjectName("muted")
        pages_row.addWidget(pages_label)
        pages_row.addStretch()
        pages_row.addWidget(self._max_pages)
        adv.addLayout(pages_row)
        self._max_pages.setRange(1, 10000)
        self._max_pages.valueChanged.connect(self._on_scope_changed)
        self._delay_spin = QDoubleSpinBox()
        adv.addLayout(_form_row(_("请求延迟(秒)"), self._delay_spin, "http.delay"))
        self._delay_spin.setRange(0, 60)
        self._delay_spin.setSingleStep(0.5)
        self._delay_spin.valueChanged.connect(self._on_scope_changed)
        self._concurrency_spin = QSpinBox()
        adv.addLayout(_form_row(_("并发数"), self._concurrency_spin, "crawl.concurrency"))
        self._concurrency_spin.setRange(1, 64)
        self._concurrency_spin.valueChanged.connect(self._on_scope_changed)
        self._trial_pages_spin = QSpinBox()
        adv.addLayout(_form_row(_("试跑页数"), self._trial_pages_spin))
        self._trial_pages_spin.setRange(_TRIAL_PAGES_MIN, _TRIAL_PAGES_MAX)
        self._trial_pages_spin.setValue(_TRIAL_PAGES_DEFAULT)
        self._trial_pages_spin.setToolTip(_("试跑消耗流量/触发反爬的上限，与全量运行页数分离"))
        self._trial_pages_spin.valueChanged.connect(self._on_trial_pages_changed)
        self._download_chk = QCheckBox(_("下载附件 / PDF"))
        self._download_chk.toggled.connect(self._on_scope_changed)
        dl_row = QHBoxLayout()
        dl_row.addWidget(HelpTooltip("download.files"))
        dl_row.addWidget(self._download_chk)
        adv.addLayout(dl_row)
        self._pdf_chk = QCheckBox(_("处理 PDF（OCR）"))
        self._pdf_chk.toggled.connect(self._on_scope_changed)
        pdf_row = QHBoxLayout()
        pdf_row.addWidget(HelpTooltip("processors.pdf"))
        pdf_row.addWidget(self._pdf_chk)
        adv.addLayout(pdf_row)
        self._monitor_chk = QCheckBox(_("变化监测（定时比较同址内容）"))
        self._monitor_chk.toggled.connect(self._on_schedule_changed)
        mon_row = QHBoxLayout()
        mon_row.addWidget(HelpTooltip("updates.same_url"))
        mon_row.addWidget(self._monitor_chk)
        adv.addLayout(mon_row)
        body.addWidget(self._advanced_box)

    def _toggle_advanced(self) -> None:
        visible = not self._advanced_box.isVisible()
        self._advanced_box.setVisible(visible)
        self._advanced_btn.setText(_("高级设置 ▼") if visible else _("高级设置 ▶"))

    def _on_trial_pages_changed(self) -> None:
        self._trial_btn.setText(_("先试跑 {0} 页").format(self._trial_pages_spin.value()))

    # ------------------------------------------------------------------
    #  ③ 字段区
    # ------------------------------------------------------------------
    def _build_fields_area(self) -> None:
        body = self._fields_section.body()
        hint_row = QHBoxLayout()
        hint_row.addWidget(HelpTooltip("fields.definition"))
        hint_row.addWidget(HelpTooltip("selection.topic"))
        hint = QLabel(_("字段可留空——内核会自动提取标题、正文等通用内容"))
        hint.setObjectName("muted")
        hint_row.addWidget(hint)
        hint_row.addStretch()
        body.addLayout(hint_row)

        # P1：智能补全主按钮（动态路径标注：AI 轨为「智能补全」，无 AI 轨为「启发式补全」）
        self._complete_btn = QPushButton(_("⚙️ 启发式补全字段"))
        self._complete_btn.setProperty("primary", True)
        self._complete_btn.setToolTip(_("根据站点类型/模板补充常见字段；只去重追加，绝不覆盖你已有的字段"))
        self._complete_btn.clicked.connect(self._heuristic_complete_fields)
        # P4：视觉点选提升为主流程（专业/开发者可见，简单模式隐藏）
        self._visual_pick_btn = QPushButton(_("👆 视觉点选"))
        self._visual_pick_btn.setToolTip(_("打开可视化选字段：输入网址后点选目标元素生成字段；只追加不覆盖"))
        self._visual_pick_btn.clicked.connect(self._visual_pick)
        pick_row = QHBoxLayout()
        pick_row.addWidget(self._complete_btn)
        pick_row.addWidget(self._visual_pick_btn)
        body.addLayout(pick_row)

        self._fields_model = _FieldTableModel(self)
        # 用户编辑单元格 → 标 field 脏（dataChanged；增删由显式调用方负责）
        self._fields_model.dataChanged.connect(self._on_field_changed)
        # 数据整体替换/展开 → 同步「还有 N 个字段」按钮
        self._fields_model.layoutChanged.connect(self._update_more_fields_btn)
        self._fields_table = QTableView()
        self._fields_table.setModel(self._fields_model)
        self._fields_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self._fields_table.setWordWrap(False)
        self._fields_table.verticalHeader().setVisible(False)
        header = self._fields_table.horizontalHeader()
        header.setStretchLastSection(True)
        header.resizeSection(0, 140)
        header.resizeSection(1, 260)
        header.resizeSection(2, 80)
        body.addWidget(self._fields_table)

        # P2：渐进披露（PRD §3.3）——字段数 >10 时先显示前 10 条
        self._more_fields_btn = QPushButton(_("还有 0 个字段，点击加载"))
        self._more_fields_btn.setFlat(True)
        self._more_fields_btn.setVisible(False)
        self._more_fields_btn.clicked.connect(self._show_all_fields)
        body.addWidget(self._more_fields_btn)

        row = QHBoxLayout()
        add_btn = QPushButton(_("＋ 添加字段"))
        add_btn.clicked.connect(self._add_field)
        row.addWidget(add_btn)
        del_btn = QPushButton(_("－ 删除选中"))
        del_btn.clicked.connect(self._delete_field)
        row.addWidget(del_btn)
        row.addStretch()
        body.addLayout(row)

    def _add_field(self) -> None:
        self._fields_model.append(FieldDef(name=_("新字段"), selector=".example", selector_type="css"))
        last = self._fields_model.rowCount() - 1
        self._fields_table.selectRow(last)
        self._fields_table.scrollToBottom()
        self._on_field_changed()

    def _delete_field(self) -> None:
        row = self._fields_table.currentIndex().row()
        if row < 0:
            ToastManager.instance().info(_("请先选中要删除的字段"))
            return
        fields = self._fields_model.rows()
        name = fields[row].name if row < len(fields) else _("未命名")
        answer = QMessageBox.question(
            self, _("删除字段"),
            _("将移除字段「{0}」及已抓取数据。确定删除？").format(name),
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._fields_model.remove_row(row)
            self._on_field_changed()

    def _show_all_fields(self) -> None:
        """「还有 N 个字段，点击加载」：展开全部（PRD §3.3 渐进披露）。"""
        self._fields_model.show_all()
        self._update_more_fields_btn()

    def _update_more_fields_btn(self) -> None:
        hidden = self._fields_model.hidden_count()
        self._more_fields_btn.setText(_("还有 {0} 个字段，点击加载").format(hidden))
        self._more_fields_btn.setVisible(hidden > 0)

    # ------------------------------------------------------------------
    #  P1：智能补全——去重追加（Upsert，绝不覆盖用户字段，PRD §3.3）
    # ------------------------------------------------------------------
    def _heuristic_complete_fields(self) -> None:
        """本地启发式补全：模板字段优先、通用规则兜底；只追加不覆盖。

        同名但选择器不同的项保留用户版本（跳过，不写入），与 AI 轨共用同一 Upsert 语义。
        """
        existing = self._current_field_names()
        additions: list[FieldDef] = []
        template_id = getattr(self._recommendation, "template_id", "") or ""
        if template_id:
            additions = self._template_fields(template_id)
        if not additions:
            additions = list(_GENERIC_FIELD_RULES)

        added = 0
        skipped = 0
        for field in additions:
            if field.name in existing:
                skipped += 1
                continue
            self._append_field_row(field)
            existing.add(field.name)
            added += 1

        if added:
            self._on_field_changed()
            source = _("模板") if template_id else _("通用规则")
            ToastManager.instance().success(
                _("已按{0}补充 {1} 个字段（跳过 {2} 个重复）").format(source, added, skipped)
            )
        elif skipped:
            ToastManager.instance().info(_("字段已完整，无需补充（跳过 {0} 个同名项）").format(skipped))
        else:
            ToastManager.instance().info(_("暂无可补充的字段"))

    # ------------------------------------------------------------------
    #  P4：视觉点选——提升为主流程（复用 VisualFieldDialog，Upsert 追加）
    # ------------------------------------------------------------------
    def _visual_pick(self) -> None:
        """打开可视化选字段对话框；候选字段按去重追加写入，绝不覆盖用户字段。"""
        if self._locked:
            return
        urls = self._config.seed_urls or [self._url_edit.text().strip()]
        url = urls[0] if urls else ""
        if not url:
            ToastManager.instance().info(_("请先在意图区填写网址，再使用视觉点选"))
            return
        # 懒加载：避免在画布导入时连带加载 wizard 模块链（含 field_designer 等重依赖）
        from ..wizard.step3_fields import VisualFieldDialog

        dialog = VisualFieldDialog(url, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._apply_visual_candidates(dialog.selected_candidates)

    def _apply_visual_candidates(self, candidates: list[Any]) -> None:
        """视觉点选候选 → 字段表（Upsert：同名加后缀递增，绝不覆盖已有字段，PRD §3.3）。"""
        existing = self._current_field_names()
        added = 0
        for candidate in candidates:
            base = str(getattr(candidate, "suggested_name", "") or _("字段"))
            name = base
            suffix = 2
            while name in existing:
                name = f"{base}_{suffix}"
                suffix += 1
            existing.add(name)
            css = str(getattr(candidate, "css", "") or "")
            xpath = str(getattr(candidate, "xpath", "") or "")
            selector = css or xpath
            kind = _selector_kind(selector)
            self._fields_model.append(FieldDef(
                name=name,
                selector=selector,
                selector_type=kind,
                attribute=getattr(candidate, "attribute", None),
                fallback_xpath=xpath if kind == "css" and xpath else None,
            ))
            added += 1
        if added:
            self._on_field_changed()
            ToastManager.instance().success(_("已从视觉点选添加 {0} 个字段").format(added))

    def _current_field_names(self) -> set[str]:
        """当前字段表中已存在的字段名（用于去重追加）。"""
        return self._fields_model.field_names()

    def _append_field_row(self, field: FieldDef) -> None:
        """追加一行字段（model.append 触发 rowsInserted，仅一次标脏由调用方负责）。"""
        self._fields_model.append(field)

    def _template_fields(self, template_id: str) -> list[FieldDef]:
        """从模板目录读取推荐模板的预置字段（本地零网络；失败返回空表）。"""
        try:
            from ...core.runtime_paths import package_resource
            from ...templates.template_catalog import TemplateCatalog

            catalog = TemplateCatalog(package_resource("omnicrawl", "templates"))
            record = catalog.get(template_id)
            if record is None:
                return []
            fields_map = (record.config.get("extract") or {}).get("fields") or {}
        except Exception:  # noqa: BLE001 — 模板字段读取失败退回通用规则
            return []
        fields: list[FieldDef] = []
        for name, spec in fields_map.items():
            if not isinstance(spec, dict):
                fields.append(FieldDef(name=str(name), selector="", selector_type="css"))
                continue
            fields.append(FieldDef(
                name=str(name),
                selector=str(spec.get("selector", "")),
                selector_type=str(spec.get("type", "css")),
            ))
        return fields

    # ------------------------------------------------------------------
    #  ④ 验证区
    # ------------------------------------------------------------------
    def _build_trial_statusbar(self) -> QWidget:
        """验证区底部状态栏（PRD §2.4）：折叠时仍常驻，显示最近试跑摘要 + 查看详情。"""
        bar = QFrame()
        bar.setObjectName("trialStatusbar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(SPACING["md"], SPACING["xs"], SPACING["md"], SPACING["xs"])
        self._status_icon = QLabel("")
        row.addWidget(self._status_icon)
        self._status_text = QLabel(_("尚未试跑：填写网址并生成草稿后，展开此区点「先试跑」"))
        self._status_text.setObjectName("muted")
        self._status_text.setWordWrap(True)
        row.addWidget(self._status_text, 1)
        self._status_view_btn = QPushButton(_("查看详情"))
        self._status_view_btn.setFlat(True)
        self._status_view_btn.setToolTip(_("展开验证区查看完整试跑结果"))
        self._status_view_btn.clicked.connect(self._expand_trial_section)
        row.addWidget(self._status_view_btn)
        return bar

    def _expand_trial_section(self) -> None:
        self._collapse_section(self._trial_section, False)

    def _build_trial_area(self) -> None:
        body = self._trial_section.body()
        trial_row = QHBoxLayout()
        trial_row.addWidget(HelpTooltip("tryrun.plan"))
        self._trial_btn = QPushButton(_("先试跑 {0} 页").format(_TRIAL_PAGES_DEFAULT))
        self._trial_btn.setProperty("primary", True)
        self._trial_btn.setToolTip(_("在独立工作区试跑，不会改变正式任务断点"))
        self._trial_btn.clicked.connect(self.trial_run_requested)
        trial_row.addWidget(self._trial_btn)
        trial_row.addStretch()
        body.addLayout(trial_row)

        self._stale_warning = QLabel(_("⚠ 字段或草稿已变更，请重新试跑"))
        self._stale_warning.setObjectName("staleWarning")
        self._stale_warning.setVisible(False)
        body.addWidget(self._stale_warning)

        self._trial_result_label = QLabel("")
        self._trial_result_label.setObjectName("summaryText")
        self._trial_result_label.setWordWrap(True)
        body.addWidget(self._trial_result_label)

        # P2：试跑历史（最近 3 次，PRD §3.4）——字段选择器调试对比
        self._history_btn = QPushButton(_("查看上次试跑记录"))
        self._history_btn.setFlat(True)
        self._history_btn.setVisible(False)
        self._history_btn.clicked.connect(self._toggle_trial_history)
        body.addWidget(self._history_btn)
        self._history_box = QLabel("")
        self._history_box.setObjectName("muted")
        self._history_box.setWordWrap(True)
        self._history_box.setVisible(False)
        body.addWidget(self._history_box)

        self._run_btn = QPushButton(_("保存并全量运行"))
        self._run_btn.setProperty("primary", True)
        self._run_btn.setEnabled(False)
        self._run_btn.setToolTip(_("请先通过试跑"))
        self._run_btn.clicked.connect(self.run_requested)
        body.addWidget(self._run_btn)

    def _toggle_trial_history(self) -> None:
        self._history_box.setVisible(not self._history_box.isVisible())
        self._history_btn.setText(_("收起试跑记录") if self._history_box.isVisible() else _("查看上次试跑记录"))

    def _record_trial_history(self, ok: bool, summary: str) -> None:
        """保留最近 3 次试跑报告（PRD §3.4 可回溯）。"""
        from datetime import datetime

        entry = {
            "ok": ok,
            "summary": summary,
            "time": datetime.now().strftime("%H:%M:%S"),
        }
        self._trial_history.append(entry)
        del self._trial_history[:-3]  # 只留最近 3 次
        lines = []
        for index, item in enumerate(self._trial_history, 1):
            mark = _("✓") if item["ok"] else _("✗")
            first_line = str(item["summary"]).splitlines()[0] if item["summary"] else _("（无摘要）")
            lines.append(_("{mark} #{index}  {time}  {first_line}").format(
                mark=mark, index=index, time=item["time"], first_line=first_line,
            ))
        self._history_box.setText("\n".join(lines))
        self._history_btn.setVisible(bool(self._trial_history))

    # ------------------------------------------------------------------
    #  ⑤ 交付区
    # ------------------------------------------------------------------
    def _build_delivery_area(self) -> None:
        body = self._delivery_section.body()
        note = QLabel(_("输出与存储在此配置；运行入口唯一在「④ 验证与运行」（需先通过试跑）"))
        note.setObjectName("muted")
        note.setWordWrap(True)
        body.addWidget(note)
        fmt_row = QHBoxLayout()
        fmt_row.addWidget(HelpTooltip("outputs.formats"))
        fmt_label = QLabel(_("输出格式"))
        fmt_label.setObjectName("muted")
        fmt_row.addWidget(fmt_label)
        fmt_row.addStretch()
        body.addLayout(fmt_row)
        self._format_checks: list[QCheckBox] = []
        for fmt_id, fmt_label in _OUTPUT_FORMATS:
            chk = QCheckBox(fmt_label)
            chk.setProperty("fmt", fmt_id)
            chk.setChecked(True)
            chk.toggled.connect(self._on_output_changed)
            self._format_checks.append(chk)
            body.addWidget(chk)

    # ------------------------------------------------------------------
    #  状态机（按域脏标记 + 试跑 field_hash 绑定，PRD §2.2.1 / §2.2.3）
    # ------------------------------------------------------------------
    _DOMAIN_SCOPE = "scope"
    _DOMAIN_FIELD = "field"
    _DOMAIN_OUTPUT = "output"
    _DOMAIN_SCHEDULE = "schedule"

    @property
    def _dirty(self) -> bool:
        """聚合脏状态（兼容旧调用：任一域脏即视为有未提交修改）。"""
        return bool(self._dirty_domains)

    def _clear_dirty(self) -> None:
        """清除全部域脏标记（仅「采纳建议/确认修改/加载覆盖」时调用；保存草稿不清）。"""
        self._dirty_domains.clear()

    def _mark_dirty(self, domain: str = _DOMAIN_SCOPE) -> None:
        if self._updating:
            return
        if self._locked:
            return
        self._dirty_domains.add(domain)
        if self._trial_ok:
            self._set_trial_state(False, _("字段或草稿已变更，请重新试跑"))
        self.config_changed.emit()

    def _sync_form_to_config(self) -> None:
        """把画布当前控件值写回 _config（表单→配置单向同步，YAML 仍是唯一持久事实）。

        无 AI 轨道的根基：用户手动改的任何控件都必须落到配置对象，
        否则「先试跑再全量运行」用的会是过期配置。
        """
        cfg = self._config
        url = self._url_edit.text().strip()
        if url:
            cfg.seed_urls = [url]
        elif cfg.seed_urls:
            cfg.seed_urls = []
        cfg.task_description = self._desc_edit.text().strip()
        cfg.max_pages = self._max_pages.value()
        cfg.delay = self._delay_spin.value()
        cfg.concurrency = self._concurrency_spin.value()
        cfg.download.enabled = self._download_chk.isChecked()
        cfg.process_pdf = self._pdf_chk.isChecked()
        cfg.monitor_same_url = self._monitor_chk.isChecked()
        cfg.incremental = self._monitor_chk.isChecked()
        fields: list[FieldDef] = []
        for f in self._fields_model.rows():
            if not f.name.strip():
                continue
            fields.append(FieldDef(
                name=f.name.strip(),
                selector=f.selector,
                selector_type=f.selector_type if f.selector_type in ("css", "xpath", "jsonpath") else "css",
            ))
        cfg.fields = fields
        cfg.output_formats = [
            chk.property("fmt") for chk in self._format_checks if chk.isChecked()
        ]

    def _on_scope_changed(self, *_args: Any) -> None:
        """URL/范围/预算类控件变更：同步配置 + 标 scope 脏。"""
        if self._updating or self._locked:
            return
        self._sync_form_to_config()
        self._mark_dirty(self._DOMAIN_SCOPE)

    def _on_field_changed(self, *_args: Any) -> None:
        """字段表格变更：同步配置 + 标 field 脏（独立于输出/调度域）。"""
        if self._updating or self._locked:
            return
        self._sync_form_to_config()
        self._update_more_fields_btn()
        self._mark_dirty(self._DOMAIN_FIELD)

    def _on_output_changed(self, *_args: Any) -> None:
        """输出格式变更：同步配置 + 标 output 脏（不阻塞 AI 字段增强）。"""
        if self._updating or self._locked:
            return
        self._sync_form_to_config()
        self._mark_dirty(self._DOMAIN_OUTPUT)

    def _on_schedule_changed(self, *_args: Any) -> None:
        """调度/监测变更：同步配置 + 标 schedule 脏。"""
        if self._updating or self._locked:
            return
        self._sync_form_to_config()
        self._mark_dirty(self._DOMAIN_SCHEDULE)

    def _set_trial_state(self, ok: bool, summary: str = "") -> None:
        self._trial_ok = ok
        self._stale_warning.setVisible(not ok and bool(summary))
        if summary:
            self._trial_result_label.setText(summary)
        elif ok:
            self._trial_result_label.setText(_("✓ 试跑通过：可保存并全量运行"))
        self._run_btn.setEnabled(ok and not self._locked)
        self._run_btn.setToolTip(_("试跑通过后可运行") if ok else _("请先通过试跑"))
        # P2：折叠态底部状态栏始终同步最新状态（PRD §2.4 验证区永不消失）
        if ok:
            self._status_icon.setText("✓ ")
            first_line = str(summary).splitlines()[0] if summary else _("试跑通过：可保存并全量运行")
            self._status_text.setText(_("最近试跑：{0}").format(first_line))
        elif summary:
            self._status_icon.setText("⚠ ")
            self._status_text.setText(str(summary).splitlines()[0])
        else:
            self._status_icon.setText("")
            self._status_text.setText(_("尚未试跑：填写网址并生成草稿后，展开此区点「先试跑」"))

    def _sync_ui_state(self) -> None:
        locked = self._locked
        self._save_btn.setEnabled(not locked)
        self._save_btn.setToolTip(_("画布锁定中，请先完成当前操作") if locked else _("随时可保存，无需先试跑；不改变编辑状态"))
        self._start_btn.setEnabled(bool(self._url_edit.text().strip()) and not locked)
        for widget in (self._url_edit, self._desc_edit, self._fields_table,
                       self._max_pages, self._delay_spin, self._concurrency_spin,
                       self._trial_pages_spin, self._download_chk, self._pdf_chk,
                       self._monitor_chk):
            widget.setEnabled(not locked)
        for chk in self._format_checks:
            chk.setEnabled(not locked)
        self._trial_btn.setEnabled(not locked)
        self._run_btn.setEnabled(self._trial_ok and not locked)
        if locked:
            self._advanced_box.setVisible(True)  # 锁定态不隐藏高级区，避免状态漂移

    # ------------------------------------------------------------------
    #  对外接口（main.py 接线）
    # ------------------------------------------------------------------
    def load_config(self, config: CrawlConfig) -> None:
        """外部配置（YAML 编辑器/打开文件）同步后重载画布；视为确认态，清脏。"""
        self._config = config
        self._clear_dirty()
        self._trial_ok = False
        self._set_trial_state(False)
        self._reset_recommendation()
        self._rebuild_from_config()

    def apply_draft(self, draft: Any) -> None:
        """应用任务草稿（QuickTaskDraft/NaturalLanguageDraft 等）。"""
        if draft is None:
            return
        url = getattr(draft, "url", None) or ""
        intent = getattr(draft, "intent", "") or ""
        self._config.seed_urls = [url] if url else []
        self._config.task_intent = intent
        self._config.source_kind = getattr(draft, "source_kind", "static_html") or "static_html"
        self._config.max_pages = int(getattr(draft, "max_pages", 10) or 10)
        self._config.download.enabled = bool(getattr(draft, "download_files", False))
        self._config.process_pdf = bool(getattr(draft, "process_pdf", False))
        self._config.monitor_same_url = bool(getattr(draft, "monitor_changes", False))
        self._config.incremental = bool(getattr(draft, "monitor_changes", False))
        formats = getattr(draft, "output_formats", ())
        if formats:
            self._config.output_formats = list(formats)
        if url:
            self._url_edit.blockSignals(True)
            self._url_edit.setText(url)
            self._url_edit.blockSignals(False)
        self._rebuild_from_config()
        self._set_source_badge(draft)
        self._clear_dirty()
        self._set_trial_state(False)
        self._sync_ui_state()

    def set_trial_result(self, ok: bool, summary: str) -> None:
        """main 侧试跑完成后的回调；ok 时记录本次试跑的字段指纹（PRD §2.2.3）。"""
        if ok:
            self._trial_field_hash = self._field_fingerprint()
        else:
            self._trial_field_hash = None
        # P2：每次试跑都入历史（最近 3 次可回溯，PRD §3.4）
        self._record_trial_history(ok, summary)
        self._set_trial_state(ok, summary)

    def trial_matches_fields(self) -> bool:
        """运行前一致校验：试跑通过 且 试跑时的字段集与当前字段集指纹一致。"""
        return (
            bool(self._trial_ok)
            and self._trial_field_hash is not None
            and self._trial_field_hash == self._field_fingerprint()
        )

    def _field_fingerprint(self) -> str:
        """字段指纹：字段名 + 选择器 + 类型的有序序列化 MD5（忽略顺序无关字段属性）。"""
        import hashlib

        parts = [
            f"{f.name}\x1f{f.selector}\x1f{f.selector_type}" for f in self._config.fields
        ]
        return hashlib.md5("\n".join(parts).encode("utf-8")).hexdigest()

    def notify_external_edit(self, updated_config: CrawlConfig | None) -> None:
        """YAML 编辑器外部编辑回调：无冲突静默同步，有冲突锁定 + 二选一。"""
        if updated_config is None:
            return
        if not self._dirty:
            self.load_config(updated_config)
            return
        self.set_locked(True)
        box = QMessageBox(self)
        box.setWindowTitle(_("检测到外部 YAML 编辑"))
        box.setText(_("YAML 编辑器已修改配置，而画布也有未提交的修改。"))
        box.setInformativeText(_("选择如何处理："))
        load_btn = box.addButton(_("加载 YAML 覆盖草稿"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(_("放弃 YAML，保留画布"), QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() == load_btn:
            self.load_config(updated_config)
            # 覆盖即清理：废弃一切挂起的 AI/系统建议（P0 无 AI 建议，占位保证语义）
            ToastManager.instance().info(_("已加载外部配置，挂起的 AI 建议已清除"))
        else:
            # 保留画布编辑态，继续阻断旧回写（标 scope 脏）
            self._mark_dirty(self._DOMAIN_SCOPE)
        self.set_locked(False)

    def set_locked(self, locked: bool) -> None:
        self._locked = locked
        if locked:
            self._probe_timer.stop()
            self._dismiss_welcome_tip()
        self._sync_ui_state()

    def is_locked(self) -> bool:
        return self._locked

    def restart(self) -> None:
        """清空画布重来（等价"新建"）。"""
        self._config = CrawlConfig()
        self._clear_dirty()
        self._trial_ok = False
        self._set_trial_state(False)
        self._reset_recommendation()
        self._probe_timer.stop()
        self._set_probe_badge("")
        self._dismiss_welcome_tip()
        # P4：重启即废弃挂起的 AI 计划（迟到结果经 sender 守卫丢弃）
        self._plan_worker = None
        self._ai_plan_draft = None
        self._plan_card.setVisible(False)
        self._trial_history.clear()
        self._history_btn.setVisible(False)
        self._history_box.setVisible(False)
        self._url_edit.blockSignals(True)
        self._url_edit.clear()
        self._desc_edit.blockSignals(True)
        self._desc_edit.clear()
        self._url_edit.blockSignals(False)
        self._desc_edit.blockSignals(False)
        self._rebuild_from_config()
        for section in (self._draft_section, self._fields_section, self._trial_section, self._delivery_section):
            self._collapse_section(section, True)
        self._sync_ui_state()

    def focus_url_input(self) -> None:
        self._url_edit.setFocus()
        self._url_edit.selectAll()

    def set_simple_mode(self, enabled: bool) -> None:
        self._simple_mode = enabled
        self._advanced_btn.setVisible(not enabled)
        self._advanced_box.setVisible(not enabled)
        # P4：视觉点选为进阶入口，简单模式隐藏（保持极简心智，PRD §3.3 模式可见性）
        self._visual_pick_btn.setVisible(not enabled)
        # 简单模式草稿卡片只显示核心摘要（网址/采集方式/预计页数）
        self._render_summary()

    def get_config(self) -> CrawlConfig:
        return self._config

    def trial_pages(self) -> int:
        """当前试跑页数（验证区高级设置），供 main 侧 SampleRunWorker 使用。"""
        return self._trial_pages_spin.value()

    # ------------------------------------------------------------------
    #  渲染
    # ------------------------------------------------------------------
    def _rebuild_from_config(self) -> None:
        self._updating = True
        try:
            cfg = self._config
            if cfg.seed_urls:
                self._url_edit.blockSignals(True)
                self._url_edit.setText(cfg.seed_urls[0])
                self._url_edit.blockSignals(False)
            if cfg.task_description:
                self._desc_edit.blockSignals(True)
                self._desc_edit.setText(cfg.task_description)
                self._desc_edit.blockSignals(False)
            self._max_pages.setValue(cfg.max_pages)
            self._delay_spin.setValue(cfg.delay)
            self._concurrency_spin.setValue(cfg.concurrency)
            self._download_chk.setChecked(cfg.download.enabled)
            self._pdf_chk.setChecked(cfg.process_pdf)
            self._monitor_chk.setChecked(cfg.monitor_same_url)
            self._render_fields(cfg)
            self._render_formats(cfg)
            self._render_summary()
            for section in (self._draft_section, self._fields_section, self._trial_section, self._delivery_section):
                self._collapse_section(section, False)
        finally:
            self._updating = False

    def _render_summary(self) -> None:
        """草稿计划卡片：分节渲染 + 每节「可修改」标记（PRD §3.2）。

        简单模式只展示核心 3 项（入口 / 采集方式 / 预计页数），
        长尾参数折叠进「高级设置」，避免术语摊开。
        """
        cfg = self._config
        url = cfg.seed_urls[0] if cfg.seed_urls else "—"
        kind = _SOURCE_KIND_SHORT.get(cfg.source_kind, cfg.source_kind)
        editable = f"<small style='color:gray'>{_('可修改')}</small>"
        lines: list[str] = []
        lines.append(f"<b>{_('入口')}</b>　{url}　{editable}")
        lines.append(f"<b>{_('采集方式')}</b>　{kind}　{editable}")
        lines.append(f"<b>{_('预计页数')}</b>　{cfg.max_pages}　{editable}")
        if not self._simple_mode:
            lines.append(
                f"<b>{_('附件与PDF')}</b>　{_('启用') if cfg.download.enabled else _('关闭')}　{editable}"
            )
            lines.append(f"<b>{_('PDF 处理')}</b>　{_('启用') if cfg.process_pdf else _('关闭')}　{editable}")
            lines.append(
                f"<b>{_('变化监测')}</b>　{_('启用') if cfg.monitor_same_url else _('关闭')}　{editable}"
            )
            formats = "、".join(cfg.output_formats)
            lines.append(f"<b>{_('输出格式')}</b>　{formats or _('未选')}　{editable}")
            lines.append(
                f"<b>{_('资源预算')}</b>　{_('并发')} {cfg.concurrency} · "
                f"{_('延迟')} {cfg.delay}s · {_('试跑')} {self._trial_pages_spin.value()} {_('页')}　{editable}"
            )
        self._summary_label.setText("<br>".join(lines))

    def _render_fields(self, cfg: CrawlConfig) -> None:
        self._fields_model.set_fields(list(cfg.fields))
        self._update_more_fields_btn()

    def _render_formats(self, cfg: CrawlConfig) -> None:
        formats = set(cfg.output_formats)
        for chk in self._format_checks:
            chk.setChecked(chk.property("fmt") in formats)

    def _set_source_badge(self, draft: Any) -> None:
        source = getattr(draft, "hit_source", "") or ""
        badge = ""
        if source == "ai":
            badge = "🤖 " + _("AI 推荐")
        elif source in ("template", "scene"):
            badge = "📦 " + _("模板生成")
        else:
            badge = "👆 " + _("手动 / 本地生成")
        self._source_badge.setText(badge)

    # ------------------------------------------------------------------
    #  N4：选用场景（懒加载 SceneStore）——槽位生成字段 + 写 extract.scene
    # ------------------------------------------------------------------
    def _refresh_scenes(self) -> None:
        """懒加载场景列表填充下拉；SceneStore 不可用则禁用，不阻断主流程。"""
        self._scene_combo.blockSignals(True)
        self._scene_combo.clear()
        self._scene_combo.addItem(_("（不使用场景）"), "")
        try:
            from pathlib import Path

            from ...state.scene_store import SceneStore

            db = Path(self._config.workspace).expanduser() / "scene.sqlite3"
            if db.exists():
                with SceneStore(db) as store:
                    for scene in store.list_scenes():
                        self._scene_combo.addItem(scene["scene"], scene["scene"])
                self._scene_combo.setEnabled(True)
        except Exception:  # noqa: BLE001 — 场景不可用不阻断主流程
            self._scene_combo.setEnabled(False)
        self._scene_combo.blockSignals(False)

    def _on_scene_changed(self, index: int) -> None:
        """选定场景 → 槽位生成字段 + 写 extract.scene（passthrough 透传）。"""
        if self._updating or self._locked:
            return
        scene = self._scene_combo.itemData(index) or ""
        if not scene:
            return
        fields: list[FieldDef] = []
        skipped: list[str] = []
        try:
            from pathlib import Path

            from ...state.scene_store import SceneStore

            db = Path(self._config.workspace).expanduser() / "scene.sqlite3"
            if db.exists():
                with SceneStore(db) as store:
                    for slot in store.get_slots(scene):
                        if slot.extractor_type not in ("css", "xpath", "jsonpath") or not slot.pattern:
                            skipped.append(slot.slot_key)
                            continue
                        fields.append(FieldDef(
                            name=slot.slot_key,
                            selector=slot.pattern,
                            selector_type=(
                                slot.extractor_type
                                if slot.extractor_type in ("css", "xpath", "jsonpath")
                                else "css"
                            ),
                        ))
        except Exception:  # noqa: BLE001 — 场景数据加载失败不阻断
            ToastManager.instance().warning(_("场景数据加载失败"))
            return
        if fields:
            self._fields_model.set_fields(fields)
            self._on_field_changed()
        # N4：extract.scene 经 passthrough 透传（config_serializer 深合并保留）
        self._config.passthrough.setdefault("extract", {})["scene"] = scene
        if skipped:
            ToastManager.instance().info(
                _("已应用场景 {0}；跳过不支持槽位：{1}").format(scene, "、".join(skipped))
            )
        else:
            ToastManager.instance().success(_("已应用场景 {0} 的字段与基因增强").format(scene))
        self._on_scope_changed()

    # ------------------------------------------------------------------
    #  P1：模板推荐闸门前移（本地 L1/L2，零网络；L3 嗅探默认关闭不注入 fetcher）
    # ------------------------------------------------------------------
    def _refresh_recommendation(self) -> None:
        """对种子 URL 做本地分类（L1 扩展名硬止损 + L2 本地 YAML 映射），
        在草稿区给出推荐模板 + 忽略/覆盖下拉（PRD §3.2 闸门前移）。

        设计：GUI 侧不传 fetcher → 永不触发 L3 网络嗅探，符合项目安全约束。
        失败静默降级为「手动配置」，绝不阻断主流程。
        """
        self._recommendation = None
        urls = list(getattr(self._config, "seed_urls", []) or [])
        if not urls:
            self._rec_label.setText("")
            self._template_combo.setEnabled(False)
            self._ignore_rec_btn.setEnabled(False)
            return
        # Lazy 导入：避免画布模块 import 时过早读取分类 YAML（副作用）
        try:
            from omnicrawl.core import categorizer as _cat_mod
            from omnicrawl.core.categorizer import (
                RecommendationConfirmationEngine,
                SiteCategorizer,
            )
        except Exception:  # noqa: BLE001 — 推荐不可用不阻断主流程
            self._rec_label.setText("")
            return
        try:
            sc = SiteCategorizer()
            summary = sc.classify(urls, catalog=None, fetcher=None)
            engine = RecommendationConfirmationEngine()
            gate = engine.process(summary)
        except Exception:  # noqa: BLE001
            self._rec_label.setText(_("模板推荐不可用"))
            return
        rows = list(gate.auto_rows) + list(gate.human_rows)
        if not rows:
            self._rec_label.setText("")
            return
        rec, _decision = rows[0]
        self._recommendation = rec

        # 填充覆盖下拉：空=自动推荐；其余为 categorizer 已知模板常量
        self._template_combo.blockSignals(True)
        self._template_combo.clear()
        self._template_combo.addItem(_("（自动推荐）"), "")
        for const_name in sorted(dir(_cat_mod)):
            if const_name.startswith("_T_") or const_name == "_FINAL_FALLBACK_TEMPLATE":
                value = getattr(_cat_mod, const_name)
                if isinstance(value, str) and value:
                    self._template_combo.addItem(value, value)
        override = self._config.per_url_template_overrides.get(rec.url, "")
        if override:
            idx = self._template_combo.findData(override)
            if idx >= 0:
                self._template_combo.setCurrentIndex(idx)
        self._template_combo.blockSignals(False)
        self._template_combo.setEnabled(True)
        self._ignore_rec_btn.setEnabled(True)
        self._reject_btn.setEnabled(True)

        # 徽标：已有手动覆盖则显示覆盖，否则显示推荐
        if override:
            self._source_badge.setText(_("👆 手动覆盖：{0}").format(override))
        else:
            self._render_recommendation_badge(rec)
        self._rec_label.setText(
            _("将按「{0}」模板配置（置信度 {1:.2f}，来源 {2}）").format(
                rec.template_id, rec.confidence, rec.hit_source,
            )
        )

    def _render_recommendation_badge(self, rec: Any) -> None:
        self._source_badge.setText(
            _("📦 模板 {0}（置信度 {1:.2f}）").format(rec.template_id, rec.confidence)
        )

    def _on_template_override_changed(self, index: int) -> None:
        """覆盖下拉变更：写/清 per_url_template_overrides（强信号，对应域标脏）。"""
        if self._recommendation is None:
            return
        url = self._recommendation.url
        template_id = self._template_combo.itemData(index) or ""
        if template_id:
            self._config.per_url_template_overrides[url] = template_id
            self._source_badge.setText(_("👆 手动覆盖：{0}").format(template_id))
        else:
            self._config.per_url_template_overrides.pop(url, None)
            self._render_recommendation_badge(self._recommendation)
        self._on_scope_changed()

    def _ignore_recommendation(self) -> None:
        """忽略推荐：清覆盖、隐藏推荐行，按当前草稿手动配置。"""
        if self._recommendation is None:
            return
        url = self._recommendation.url
        self._config.per_url_template_overrides.pop(url, None)
        self._recommendation = None
        self._template_combo.setEnabled(False)
        self._ignore_rec_btn.setEnabled(False)
        self._reject_btn.setEnabled(False)
        self._rec_label.setText("")
        self._source_badge.setText(_("👆 手动配置"))
        self._on_scope_changed()
        ToastManager.instance().info(_("已忽略模板推荐，按当前草稿手动配置"))

    def _collect_rejection(self) -> None:
        """「👎 不准确」：弹出预设标签菜单，选择后记录诊断快照（PRD §3.2）。"""
        if self._recommendation is None:
            return
        from PyQt6.QtWidgets import QMenu

        from ...quality.template_feedback import REJECT_LABELS

        menu = QMenu(self._reject_btn)
        for label in REJECT_LABELS:
            menu.addAction(label, lambda lbl=label: self._record_rejection(lbl))
        menu.exec(self._reject_btn.mapToGlobal(self._reject_btn.rect().bottomLeft()))

    def _record_rejection(self, label: str) -> None:
        """采集一条拒绝理由：携带完整诊断快照，无快照不入库（PRD §3.2）。"""
        rec = self._recommendation
        if rec is None:
            return
        try:
            from urllib.parse import urlparse

            from ...quality.template_feedback import TemplateFeedbackStore, TemplateRejectionSnapshot

            url = getattr(rec, "url", "") or (self._config.seed_urls[0] if self._config.seed_urls else "")
            domain = urlparse(url).netloc if url else ""
            fields = tuple(f.name for f in self._config.fields)
            snapshot = TemplateRejectionSnapshot(
                url=url,
                domain=domain,
                category=getattr(rec, "reason", ""),
                confidence=float(getattr(rec, "confidence", 0.0) or 0.0),
                hit_source=getattr(rec, "hit_source", ""),
                template_id=rec.template_id,
                template_fields=fields,
                action="template_rejection",
                reject_label=label,
                field_count=len(fields),
            )
            # 无快照（缺 domain/template_id）不入库，静默提示
            if not snapshot.domain or not snapshot.template_id:
                ToastManager.instance().warning(_("缺少诊断快照，未记录反馈"))
                return
            root = Path(self._project_root) if self._project_root else Path.cwd()
            store = TemplateFeedbackStore(root / "workspace" / "logs" / "template_feedback.jsonl")
            if store.record(snapshot):
                ToastManager.instance().info(_("已记录反馈：{0}").format(label))
        except Exception as exc:  # noqa: BLE001 — 反馈采集绝不阻断主流程
            LOGGER.warning("记录模板反馈失败: %s", exc)  # noqa
            ToastManager.instance().warning(_("反馈记录失败"))

    def _reset_recommendation(self) -> None:
        """重置推荐 UI 与状态（重启/加载配置时调用）。"""
        self._recommendation = None
        self._rec_label.setText("")
        self._template_combo.blockSignals(True)
        self._template_combo.clear()
        self._template_combo.blockSignals(False)
        self._template_combo.setEnabled(False)
        self._ignore_rec_btn.setEnabled(False)
        self._reject_btn.setEnabled(False)

    def _collapse_section(self, section: _Section, collapsed: bool) -> None:
        section._collapsed = collapsed
        section._fold_btn.setText(_("展开") if collapsed else _("收起"))
        # 折叠只隐藏 body 内容；sticky 状态条保持常驻
        section._body_host.setVisible(not collapsed)

    # ------------------------------------------------------------------
    #  工具
    # ------------------------------------------------------------------
    @staticmethod
    def _is_valid_url(url: str) -> bool:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if parsed.scheme in ("http", "https"):
            return bool(parsed.netloc)
        if parsed.scheme == "file":
            return bool(parsed.path)
        return False

    def _apply_style(self, *_args) -> None:
        t = ThemeManager.instance().tokens
        self.setStyleSheet(f"""
            QGroupBox#taskCanvas QGroupBox {{
                border: 1px solid {t.border};
                border-radius: {RADIUS['lg']}px;
                margin-top: 8px;
                background: {t.surface};
            }}
            QGroupBox#taskCanvas QGroupBox::title {{
                subcontrol-origin: margin;
                left: {SPACING['lg']}px;
                padding: 0 {SPACING['xs']}px;
                color: {t.text};
            }}
            QLabel#pageTitle {{
                font-size: {FONT_SIZE['heading']}px;
                font-weight: 700;
                color: {t.text};
            }}
            QLabel#sectionTitle {{
                font-size: {FONT_SIZE['subtitle']}px;
                font-weight: 600;
                color: {t.text};
            }}
            QLabel#summaryText {{
                font-size: {FONT_SIZE['body']}px;
                color: {t.text};
                background: {t.canvas};
                border-radius: {RADIUS['md']}px;
                padding: {SPACING['md']}px;
            }}
            QLabel#badge {{
                font-size: {FONT_SIZE['small']}px;
                color: {t.primary};
                background: {t.selection};
                border-radius: {RADIUS['pill']}px;
                padding: 2px {SPACING['md']}px;
            }}
            QLabel#staleWarning {{
                font-size: {FONT_SIZE['body']}px;
                color: {t.warning};
                background: {t.warning_bg};
                border-radius: {RADIUS['md']}px;
                padding: {SPACING['sm']}px;
            }}
            QLabel#welcomeTip {{
                font-size: {FONT_SIZE['body']}px;
                color: {t.primary};
                background: {t.selection};
                border-radius: {RADIUS['md']}px;
                padding: {SPACING['sm']}px {SPACING['md']}px;
            }}
            QLineEdit[welcomeHighlight="true"] {{
                border: 2px solid {t.primary};
            }}
            QFrame#planCard {{
                background: {t.selection};
                border-radius: {RADIUS['md']}px;
                border: 1px solid {t.primary};
            }}
            QLabel#planCardTitle {{
                font-size: {FONT_SIZE['body']}px;
                font-weight: 700;
                color: {t.primary};
            }}
            QFrame#advancedBox {{
                background: {t.canvas};
                border-radius: {RADIUS['md']}px;
                border: 1px solid {t.border};
            }}
            QLabel#muted {{ color: {t.muted}; }}
        """)


def _form_row(
    label_text: str,
    widget: QWidget,
    help_id: str | None = None,
) -> QHBoxLayout:
    row = QHBoxLayout()
    if help_id:
        row.addWidget(HelpTooltip(help_id))
    label = QLabel(label_text)
    label.setObjectName("muted")
    row.addWidget(label)
    row.addStretch()
    row.addWidget(widget)
    return row


def _intent_from_text(text: str) -> str:
    """从一句话描述推断意图（与 natural_language_task 保持同语义的本地回退）。

    以下中文关键词是内部意图匹配词（非 UI 文案），刻意不翻译。
    """
    lowered = (text or "").lower()
    if any(word in lowered for word in ("下载", "附件", "pdf", "download")):  # noqa
        return "download_files"
    if any(word in lowered for word in ("监测", "变化", "定时", "监控")):  # noqa
        return "monitor_changes"
    if any(word in lowered for word in ("栏目", "列表", "整站", "采集整个")):  # noqa
        return "collect_section"
    return "save_page"
