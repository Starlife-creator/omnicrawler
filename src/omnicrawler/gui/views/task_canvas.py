"""任务画布（Task Canvas）— P0 画布骨架。

五区域渐进布局：意图区 → 草稿区 → 字段区 → 验证区 → 交付区。
全手动轨全流程，含 P0 硬约束：
- 运行唯一出口（交付区无运行按钮，常驻操作栏在试跑通过后启用全量运行）
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
from typing import Any

LOGGER = logging.getLogger(__name__)

from PySide6.QtCore import QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.config_model import CrawlConfig, FieldDef
from ..design_system import RADIUS, SPACING, ThemeManager, scaled_font_px
from ..i18n import _
from ..widgets.help_tooltip import HelpTooltip
from ..widgets.toast import ToastManager

# Compatibility aliases keep the canvas's private API stable for existing tests/plugins.
from .task_canvas_ai_plan import AiPlanReviewMixin
from .task_canvas_components import PlanReviewWorker as _PlanReviewWorker
from .task_canvas_components import _repolish_widget, _Section
from .task_canvas_draft import DraftAreaMixin
from .task_canvas_fields import FieldsAreaMixin
from .task_canvas_intent import IntentAreaMixin
from .task_canvas_logic import OUTPUT_FORMATS as _OUTPUT_FORMATS
from .task_canvas_logic import SOURCE_KIND_SHORT as _SOURCE_KIND_SHORT
from .task_canvas_logic import TRIAL_PAGES_DEFAULT as _TRIAL_PAGES_DEFAULT
from .task_canvas_logic import crawl_fingerprint, field_fingerprint
from .task_canvas_logic import intent_from_text as _intent_from_text
from .task_canvas_recommendation import SceneRecommendationMixin


class TaskCanvas(FieldsAreaMixin, DraftAreaMixin, IntentAreaMixin, AiPlanReviewMixin, SceneRecommendationMixin, QScrollArea):
    """持续可编辑的任务工作台。"""

    config_changed = Signal()
    save_requested = Signal()
    trial_run_requested = Signal()
    run_requested = Signal()
    yaml_view_requested = Signal()
    # P2：意图区 URL 探活（600ms 停顿后触发；结果经 set_probe_result 回填徽标）
    probe_requested = Signal(str)

    # 探活停顿（毫秒）：用户停止输入后再发起轻量探测
    _PROBE_DEBOUNCE_MS = 600
    # P3：首启引导气泡时长（毫秒），3 秒自动消失（PRD §3.1）
    _WELCOME_TIP_MS = 3000
    _WELCOME_TIP_KEY = "canvas/welcome_tip_seen"
    _ONBOARDING_KEY = "canvas/onboarding_complete"

    def __init__(
        self,
        config: CrawlConfig,
        parent: QWidget | None = None,
        *,
        project_root: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setAccessibleName(_("任务工作台"))
        self._config = config
        self._project_root = project_root
        self._updating = False
        # 按域脏标记（PRD §2.2.1）：scope=URL/范围/预算，field=字段规则，
        # output=输出格式/存储，schedule=调度/监测。AI 回写只受其对应域约束。
        self._dirty_domains: set[str] = set()
        self._locked = False
        self._trial_ok = False
        self._trial_field_hash: str | None = None
        self._trial_config_hash: str | None = None
        self._delivery_ok = True
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

        # 任务目标（常驻）
        self._intent_section = _Section(_("任务目标"))
        self._build_intent_area()
        layout.addWidget(self._intent_section)

        # 采集方案
        self._draft_section = _Section(_("采集方案"))
        self._build_draft_area()
        layout.addWidget(self._draft_section)

        # 字段规则
        self._fields_section = _Section(_("字段规则"))
        self._build_fields_area()
        layout.addWidget(self._fields_section)

        # 试跑验证；状态和主操作由工作台外层常驻操作栏承载。
        self._trial_section = _Section(_("试跑验证"))
        self._build_trial_area()
        layout.addWidget(self._trial_section)

        # 输出与交付
        self._delivery_section = _Section(_("输出与交付"))
        self._build_delivery_area()
        layout.addWidget(self._delivery_section)

        # 创建后由 MainWindow 放在滚动区外，确保长任务配置下仍始终可见。
        self._action_bar = self._build_persistent_action_bar()

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
        title = QLabel(_("任务工作台"))
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
    #  ① 意图区（含 URL 探测与新手引导）→ 见 task_canvas_intent.IntentAreaMixin
    # ------------------------------------------------------------------

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
        self._update_onboarding()

    # ------------------------------------------------------------------
    #  ② 草稿区 → 见 task_canvas_draft.DraftAreaMixin
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    #  ③ 字段规则 → 见 task_canvas_fields.FieldsAreaMixin
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    #  试跑验证
    # ------------------------------------------------------------------
    def _build_persistent_action_bar(self) -> QWidget:
        """构建工作台常驻操作栏：验证状态、试跑和全量运行唯一出口。"""
        bar = QFrame(self)
        bar.setObjectName("workspaceActionBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(SPACING["lg"], SPACING["sm"], SPACING["lg"], SPACING["sm"])
        self._status_icon = QLabel("")
        row.addWidget(self._status_icon)
        self._status_text = QLabel(_("尚未试跑：填写网址并生成草稿后开始小样本验证"))
        self._status_text.setObjectName("muted")
        self._status_text.setWordWrap(True)
        row.addWidget(self._status_text, 1)
        self._status_view_btn = QPushButton(_("查看详情"))
        self._status_view_btn.setFlat(True)
        self._status_view_btn.setToolTip(_("展开验证区查看完整试跑结果"))
        self._status_view_btn.clicked.connect(self._expand_trial_section)
        row.addWidget(self._status_view_btn)
        self._trial_btn = QPushButton(_("试跑 {0} 页").format(_TRIAL_PAGES_DEFAULT))
        self._trial_btn.setProperty("primary", True)
        self._trial_btn.setToolTip(_("在独立工作区试跑，不会改变正式任务断点"))
        self._trial_btn.clicked.connect(self.trial_run_requested)
        row.addWidget(self._trial_btn)
        row.addWidget(HelpTooltip("tryrun.plan"))
        self._run_btn = QPushButton(_("开始全量运行"))
        self._run_btn.setProperty("primary", True)
        self._run_btn.setEnabled(False)
        self._run_btn.setToolTip(_("请先通过试跑"))
        self._run_btn.clicked.connect(self.run_requested)
        row.addWidget(self._run_btn)
        return bar

    def persistent_action_bar(self) -> QWidget:
        """返回由主窗口安放在滚动区外的常驻操作栏。"""
        return self._action_bar

    def _expand_trial_section(self) -> None:
        self._collapse_section(self._trial_section, False)

    def _build_trial_area(self) -> None:
        body = self._trial_section.body()
        detail_hint = QLabel(_("这里显示最近试跑的字段命中、页面处理和错误详情。"))
        detail_hint.setObjectName("muted")
        detail_hint.setWordWrap(True)
        body.addWidget(detail_hint)

        self._trial_metrics_label = QLabel(_("完成试跑后，这里会显示页面数、记录数和诊断建议。"))
        self._trial_metrics_label.setObjectName("summaryText")
        self._trial_metrics_label.setWordWrap(True)
        body.addWidget(self._trial_metrics_label)

        self._trial_diagnosis_label = QLabel("")
        self._trial_diagnosis_label.setObjectName("staleWarning")
        self._trial_diagnosis_label.setWordWrap(True)
        self._trial_diagnosis_label.setVisible(False)
        body.addWidget(self._trial_diagnosis_label)

        fix_row = QHBoxLayout()
        self._fix_scope_btn = QPushButton(_("检查网址与范围"))
        self._fix_scope_btn.clicked.connect(self._focus_scope_settings)
        self._fix_scope_btn.setVisible(False)
        fix_row.addWidget(self._fix_scope_btn)
        self._fix_fields_btn = QPushButton(_("修正字段规则"))
        self._fix_fields_btn.clicked.connect(self._focus_field_settings)
        self._fix_fields_btn.setVisible(False)
        fix_row.addWidget(self._fix_fields_btn)
        fix_row.addStretch()
        body.addLayout(fix_row)

        self._stale_warning = QLabel(_("⚠ 采集范围或字段规则已变更，请重新试跑"))
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

    def _toggle_trial_history(self) -> None:
        self._history_box.setVisible(not self._history_box.isVisible())
        self._history_btn.setText(_("收起试跑记录") if self._history_box.isVisible() else _("查看上次试跑记录"))

    def _focus_scope_settings(self) -> None:
        self._url_edit.setFocus()
        self._url_edit.selectAll()

    def _focus_field_settings(self) -> None:
        self._collapse_section(self._fields_section, False)
        self._fields_table.setFocus()

    def _render_trial_details(self, details: dict[str, Any] | None) -> None:
        if not details:
            return
        status = str(details.get("status") or _("未知"))
        processed = int(details.get("processed", 0) or 0)
        records = int(details.get("records", 0) or 0)
        failed = int(details.get("failed", 0) or 0)
        per_page = records / processed if processed else 0.0
        self._trial_metrics_label.setText(
            _("<b>试跑概览</b><br>状态：{0}<br>处理页面：{1}<br>提取记录：{2}<br>平均每页：{3:.1f} 条").format(
                status, processed, records, per_page,
            )
        )

        diagnosis = ""
        show_scope = False
        show_fields = False
        error = str(details.get("error") or "").strip()
        if processed <= 0:
            diagnosis = _("没有成功处理页面。请检查网址、访问权限、robots 设置或页面是否需要浏览器渲染。")
            show_scope = True
        elif records <= 0:
            diagnosis = _("页面可以访问，但没有提取到记录。请检查字段选择器、列表容器或页面类型。")
            show_fields = True
        elif failed > 0:
            diagnosis = _("已提取数据，但有 {0} 个请求失败。建议检查分页范围和访问频率。").format(failed)
            show_scope = True
        elif error:
            diagnosis = _("试跑报告了错误：{0}").format(error)
            show_scope = True

        self._trial_diagnosis_label.setText(diagnosis)
        self._trial_diagnosis_label.setVisible(bool(diagnosis))
        self._fix_scope_btn.setVisible(show_scope)
        self._fix_fields_btn.setVisible(show_fields)

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
    #  输出与交付
    # ------------------------------------------------------------------
    def _build_delivery_area(self) -> None:
        body = self._delivery_section.body()
        note = QLabel(_("输出与存储在此配置；全量运行前需要先通过试跑验证。"))
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
        for fmt_id, fmt_caption in _OUTPUT_FORMATS:
            chk = QCheckBox(fmt_caption)
            chk.setProperty("fmt", fmt_id)
            chk.setChecked(True)
            chk.toggled.connect(self._on_output_changed)
            self._format_checks.append(chk)
            body.addWidget(chk)
        self._delivery_status = QLabel("")
        self._delivery_status.setObjectName("muted")
        body.addWidget(self._delivery_status)
        self._validate_delivery()

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
        if self._trial_ok and domain in {self._DOMAIN_SCOPE, self._DOMAIN_FIELD}:
            self._set_trial_state(False, _("采集范围或字段规则已变更，请重新试跑"))
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
        self._validate_delivery()
        self._mark_dirty(self._DOMAIN_OUTPUT)

    def _on_schedule_changed(self, *_args: Any) -> None:
        """调度/监测变更：同步配置 + 标 schedule 脏。"""
        if self._updating or self._locked:
            return
        self._sync_form_to_config()
        self._mark_dirty(self._DOMAIN_SCHEDULE)

    def _validate_delivery(self) -> bool:
        """本地验证交付设置；不触发网页重新试跑。"""
        selected = [chk for chk in getattr(self, "_format_checks", []) if chk.isChecked()]
        self._delivery_ok = bool(selected)
        if hasattr(self, "_delivery_status"):
            self._delivery_status.setText(
                _("✓ 交付配置有效") if self._delivery_ok else _("⚠ 至少选择一种输出格式")
            )
            self._delivery_status.setProperty("status", "success" if self._delivery_ok else "warning")
            _repolish_widget(self._delivery_status)
        if hasattr(self, "_run_btn"):
            self._run_btn.setEnabled(self._trial_ok and self._delivery_ok and not self._locked)
            if self._trial_ok and not self._delivery_ok:
                self._run_btn.setToolTip(_("请先修复输出与交付设置"))
        return self._delivery_ok

    def _set_trial_state(self, ok: bool, summary: str = "") -> None:
        self._trial_ok = ok
        self._stale_warning.setVisible(not ok and bool(summary))
        if not ok and summary:
            self._stale_warning.setText(summary)
        if summary:
            self._trial_result_label.setText(summary)
        elif ok:
            self._trial_result_label.setText(_("✓ 试跑通过：可以开始全量运行"))
        self._run_btn.setEnabled(ok and self._delivery_ok and not self._locked)
        if ok and not self._delivery_ok:
            self._run_btn.setToolTip(_("请先修复输出与交付设置"))
        else:
            self._run_btn.setToolTip(_("试跑通过后可运行") if ok else _("请先通过试跑"))
        # P2：折叠态底部状态栏始终同步最新状态（PRD §2.4 验证区永不消失）
        if ok:
            self._status_icon.setText("✓ ")
            first_line = str(summary).splitlines()[0] if summary else _("试跑通过：可以开始全量运行")
            self._status_text.setText(_("最近试跑：{0}").format(first_line))
        elif summary:
            self._status_icon.setText("⚠ ")
            self._status_text.setText(str(summary).splitlines()[0])
        else:
            self._status_icon.setText("")
            self._status_text.setText(_("尚未试跑：填写网址并生成草稿后开始小样本验证"))

    def _sync_ui_state(self) -> None:
        locked = self._locked
        self._save_btn.setEnabled(not locked)
        self._save_btn.setToolTip(_("工作台锁定中，请先完成当前操作") if locked else _("随时可保存，无需先试跑；不改变编辑状态"))
        self._start_btn.setEnabled(bool(self._url_edit.text().strip()) and not locked)
        for widget in (self._url_edit, self._desc_edit, self._fields_table,
                       self._max_pages, self._delay_spin, self._concurrency_spin,
                       self._trial_pages_spin, self._download_chk, self._pdf_chk,
                       self._monitor_chk):
            widget.setEnabled(not locked)
        for chk in self._format_checks:
            chk.setEnabled(not locked)
        self._trial_btn.setEnabled(not locked)
        self._run_btn.setEnabled(self._trial_ok and self._delivery_ok and not locked)
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

    def set_trial_result(self, ok: bool, summary: str, details: dict[str, Any] | None = None) -> None:
        """试跑完成回调：绑定配置指纹并渲染结构化诊断。"""
        if ok:
            self._trial_field_hash = self._field_fingerprint()
            self._trial_config_hash = crawl_fingerprint(self._config)
        else:
            self._trial_field_hash = None
            self._trial_config_hash = None
        # P2：每次试跑都入历史（最近 3 次可回溯，PRD §3.4）
        self._record_trial_history(ok, summary)
        self._render_trial_details(details)
        self._set_trial_state(ok, summary)
        self._collapse_section(self._trial_section, False)
        self._update_onboarding()
        if ok and not self._onboarding_card.isHidden():
            from ...gui.settings import make_qsettings

            make_qsettings("OmniCrawler", "GUIWorkbench").setValue(self._ONBOARDING_KEY, True)
            QTimer.singleShot(1800, self._onboarding_card.hide)

    def trial_matches_fields(self) -> bool:
        """兼容旧 API：验证试跑时的采集范围/规则仍与当前配置一致。"""
        return (
            bool(self._trial_ok)
            and self._trial_config_hash is not None
            and self._trial_config_hash == crawl_fingerprint(self._config)
            and self._delivery_ok
        )

    def _field_fingerprint(self) -> str:
        """字段指纹：委托纯逻辑缝（task_canvas_logic.field_fingerprint）。"""
        return field_fingerprint(self._config.fields)

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
        box.setText(_("YAML 编辑器已修改配置，而任务工作台也有未提交的修改。"))
        box.setInformativeText(_("选择如何处理："))
        load_btn = box.addButton(_("加载 YAML 覆盖草稿"), QMessageBox.ButtonRole.AcceptRole)
        box.addButton(_("放弃 YAML，保留工作台修改"), QMessageBox.ButtonRole.RejectRole)
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
        self._validate_delivery()

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

    def _apply_style(self, *_args: object) -> None:
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
                font-size: {scaled_font_px("heading")}px;
                font-weight: 700;
                color: {t.text};
            }}
            QLabel#sectionTitle {{
                font-size: {scaled_font_px("subtitle")}px;
                font-weight: 600;
                color: {t.text};
            }}
            QLabel#summaryText {{
                font-size: {scaled_font_px("body")}px;
                color: {t.text};
                background: {t.canvas};
                border-radius: {RADIUS['md']}px;
                padding: {SPACING['md']}px;
            }}
            QLabel#badge {{
                font-size: {scaled_font_px("small")}px;
                color: {t.primary};
                background: {t.selection};
                border-radius: {RADIUS['pill']}px;
                padding: 2px {SPACING['md']}px;
            }}
            QLabel#staleWarning {{
                font-size: {scaled_font_px("body")}px;
                color: {t.warning};
                background: {t.warning_bg};
                border-radius: {RADIUS['md']}px;
                padding: {SPACING['sm']}px;
            }}
            QLabel#welcomeTip {{
                font-size: {scaled_font_px("body")}px;
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
            QFrame#onboardingCard {{
                background: {t.selection};
                border-radius: {RADIUS['md']}px;
                border: 1px solid {t.primary};
            }}
            QLabel#planCardTitle {{
                font-size: {scaled_font_px("body")}px;
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
        if hasattr(self, "_action_bar"):
            self._action_bar.setStyleSheet(f"""
                QFrame#workspaceActionBar {{
                    background: {t.surface};
                    border-top: 1px solid {t.border};
                    border-radius: {RADIUS['md']}px;
                }}
                QLabel#muted {{ color: {t.muted}; }}
            """)
