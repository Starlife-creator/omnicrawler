"""TaskCanvas 的「② 草稿区」域 —— 由 task_canvas.py 抽出的 Mixin（P1-3 第五批）。

涵盖草稿区（采集方案）的构建与交互：
- ``_build_draft_area``：AI 计划审核卡片 + 种子 URL 摘要 + 采集范围 + 高级设置
  （延迟/并发/试跑页数/下载附件/PDF 处理/变化监测）
- ``_toggle_advanced`` / ``_on_trial_pages_changed``

以 Mixin 形式保留 ``self`` 语义，宿主属性结构（五区 section 等）与调用点完全不变。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from ..design_system import SPACING
from ..i18n import _
from ..widgets.help_tooltip import HelpTooltip
from .task_canvas_components import _form_row
from .task_canvas_logic import TRIAL_PAGES_DEFAULT as _TRIAL_PAGES_DEFAULT
from .task_canvas_logic import TRIAL_PAGES_MAX as _TRIAL_PAGES_MAX
from .task_canvas_logic import TRIAL_PAGES_MIN as _TRIAL_PAGES_MIN

if TYPE_CHECKING:
    from .task_canvas_components import _Section


class DraftAreaMixin:
    """② 草稿区构建与交互。"""

    # ---- 宿主契约：实例属性 ----
    _draft_section: _Section
    _advanced_btn: QPushButton
    _advanced_box: QFrame
    _summary_label: QLabel
    _max_pages: QSpinBox
    _delay_spin: QDoubleSpinBox
    _concurrency_spin: QSpinBox
    _trial_pages_spin: QSpinBox
    _trial_btn: QPushButton
    _download_chk: QCheckBox
    _pdf_chk: QCheckBox
    _monitor_chk: QCheckBox
    _plan_card: QFrame
    _plan_title: QLabel
    _plan_source_label: QLabel
    _plan_text: QLabel
    _accept_plan_btn: QPushButton
    _dismiss_plan_btn: QPushButton
    _rec_label: QLabel
    _source_badge: QLabel
    _template_combo: QComboBox
    _scene_combo: QComboBox
    _ignore_rec_btn: QPushButton
    _reject_btn: QPushButton

    if TYPE_CHECKING:
        # 由 TaskCanvas / 其他 Mixin 提供；仅类型检查期可见，运行期不存在，故不遮蔽宿主实现。
        def _on_scope_changed(self, *_args: Any) -> None: ...
        def _on_schedule_changed(self, *_args: Any) -> None: ...
        def _on_scene_changed(self, index: int) -> None: ...
        def _on_template_override_changed(self, index: int) -> None: ...
        def _accept_plan(self) -> None: ...
        def _dismiss_plan(self) -> None: ...
        def _ignore_recommendation(self) -> None: ...
        def _collect_rejection(self) -> None: ...

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
        self._trial_btn.setText(_("试跑 {0} 页").format(self._trial_pages_spin.value()))
