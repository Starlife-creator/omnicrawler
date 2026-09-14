"""TaskCanvas 的「② 草稿区」域 —— 由 task_canvas.py 抽出的 Mixin（P1-3 第五批）。

涵盖草稿区（采集方案）的构建与交互：
- ``_build_draft_area``：AI 计划审核卡片 + 种子 URL 摘要 + 采集范围 + 高级设置
  （延迟/并发/试跑页数/下载附件/PDF 处理/变化监测）
- ``_toggle_advanced`` / ``_on_trial_pages_changed``

以 Mixin 形式保留 ``self`` 语义，宿主属性结构（五区 section 等）与调用点完全不变。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.pagination import INTEGER, SHAPES, PaginationShape, detect_shape, shape_for_type
from ..design_system import SPACING
from ..i18n import _
from ..widgets.help_tooltip import HelpTooltip
from .task_canvas_components import _form_row
from .task_canvas_logic import TRIAL_PAGES_DEFAULT as _TRIAL_PAGES_DEFAULT
from .task_canvas_logic import TRIAL_PAGES_MAX as _TRIAL_PAGES_MAX
from .task_canvas_logic import TRIAL_PAGES_MIN as _TRIAL_PAGES_MIN

if TYPE_CHECKING:
    from .task_canvas_components import _Section


# 分页字段的中文标签与示例 —— **只负责「显示成什么」**。
# 字段名与取值类型来自 `core.pagination` 契约；契约新增字段时这里没登记也只会显示原始键名，
# 因此不会出现「GUI 与契约各说一套」的分页词典。
# 刻意写成函数而不是模块级字典：`_()` 要在**渲染时**调用（模块导入期翻译器尚未装载），
# 这也是本包 i18n 门禁要求的形态（gui 源码里的中文字面量必须经 `_()` 包裹）。


def _pagination_field_label(name: str) -> str:
    """分页字段的中文标签；未登记的名字原样显示（不静默失配）。"""
    return _(
        {
            "parameter": "参数名",
            "start": "起始",
            "end": "结束",
            "step": "步长",
            "next_path": "下一页字段（JSONPath）",
        }.get(name, name)
    )


def _pagination_field_placeholder(name: str) -> str:
    """分页字段的填写示例。"""
    return _(
        {
            "parameter": "如 page / cursor / offset",
            "start": "1",
            "end": "1",
            "step": "1；偏移式接口填每页条数",
            "next_path": "$.next",
        }.get(name, "")
    )


def _pagination_shape_label(key: str) -> str:
    """分页方式下拉的文案（形状 key 来自契约）。"""
    return _({"page": "按页码 / 偏移", "cursor": "按游标 / 下一页值"}.get(key, key))
#: 下拉里代表"形状未知、原样保留"的哨兵值（插件可自带 type）。
_PAGINATION_KEEP = "__keep__"
#: 由表单拥有的分页字段名（写回时据此清掉另一形状的残留；契约里 `editable=False` 的键不在此列，
#: 例如 `location` —— 它需要 `source.payload` 配套，表单改不动，就必须原样留着）。
_EDITABLE_PAGINATION_FIELDS = frozenset(
    field.name for shape in SHAPES for field in shape.fields if field.editable
)


class DraftAreaMixin:
    """② 草稿区构建与交互。"""

    # ---- 宿主契约：实例属性 ----
    _draft_section: _Section
    _advanced_btn: QPushButton
    _advanced_box: QFrame
    _summary_label: QLabel
    _max_pages: QSpinBox
    _pagination_combo: QComboBox
    _pagination_edits: dict[str, QLineEdit]
    _pagination_rows: dict[str, QWidget]
    _pagination_extras: dict[str, Any]
    _pagination_keep_original: bool
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
        _updating: bool
        _locked: bool
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

        # 分页：形状与字段来自 `core.pagination` 契约（唯一真源），GUI 不复述分页词典。
        # 此前表单**没有**分页入口 ⇒ 表单建出的任务只能抓第一批结果，且没有任何提示。
        self._pagination_extras = {}
        self._pagination_keep_original = False
        self._pagination_rows = {}
        self._pagination_edits = {}
        self._pagination_combo = QComboBox()
        self._pagination_combo.setObjectName("paginationCombo")
        self._pagination_combo.setAccessibleName(_("分页方式"))
        self._pagination_combo.setAccessibleDescription(
            _("按页码逐页取，或按响应里的下一页值继续；不翻页时只取第一批结果。")
        )
        self._pagination_combo.currentIndexChanged.connect(self._on_pagination_shape_changed)
        pagination_row = QHBoxLayout()
        pagination_row.addWidget(HelpTooltip("source.pagination"))
        pagination_label = QLabel(_("分页方式"))
        pagination_label.setObjectName("muted")
        pagination_row.addWidget(pagination_label)
        pagination_row.addStretch()
        pagination_row.addWidget(self._pagination_combo)
        adv.addLayout(pagination_row)
        for shape in SHAPES:
            for field in shape.fields:
                if not field.editable or field.name in self._pagination_edits:
                    continue
                edit = QLineEdit()
                edit.setObjectName("pagination" + field.name.title().replace("_", ""))
                edit.setPlaceholderText(_pagination_field_placeholder(field.name))
                edit.setClearButtonEnabled(True)
                edit.setAccessibleName(_pagination_field_label(field.name))
                edit.textChanged.connect(self._on_scope_changed)
                row_widget = QWidget()
                row_layout = QHBoxLayout(row_widget)
                row_layout.setContentsMargins(0, 0, 0, 0)
                row_label = QLabel(_pagination_field_label(field.name))
                row_label.setObjectName("muted")
                row_layout.addWidget(row_label)
                row_layout.addStretch()
                row_layout.addWidget(edit, 3)
                row_widget.setVisible(False)
                adv.addWidget(row_widget)
                self._pagination_rows[field.name] = row_widget
                self._pagination_edits[field.name] = edit
        self._reset_pagination_choices()
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

    # ------------------------------------------------------------------
    #  分页方式（形状来自 core.pagination 契约；GUI 只负责呈现与读写）
    # ------------------------------------------------------------------

    def _reset_pagination_choices(self) -> None:
        """按契约重建下拉项：不翻页 + 每个形状（+ 需要时的「保持原样」）。"""
        current = self._pagination_combo.currentData()
        self._pagination_combo.blockSignals(True)
        self._pagination_combo.clear()
        self._pagination_combo.addItem(_("不翻页"), "")
        for shape in SHAPES:
            self._pagination_combo.addItem(
                _pagination_shape_label(shape.key), shape.key
            )
        if self._pagination_keep_original:
            self._pagination_combo.addItem(_("其它（保持原样）"), _PAGINATION_KEEP)
        index = self._pagination_combo.findData(current if current is not None else "")
        self._pagination_combo.setCurrentIndex(max(0, index))
        self._pagination_combo.blockSignals(False)

    def _active_pagination_shape(self) -> PaginationShape | None:
        """当前下拉选中的形状；「不翻页」与「保持原样」都返回 ``None``。"""
        return shape_for_type(self._pagination_combo.currentData())

    def _update_pagination_rows(self) -> None:
        """按所选形状显隐字段行 —— 其它形状的字段不出现，避免让人以为它还在生效。"""
        shape = self._active_pagination_shape()
        names = {field.name for field in shape.fields if field.editable} if shape else set()
        for name, row_widget in self._pagination_rows.items():
            row_widget.setVisible(name in names)

    def _on_pagination_shape_changed(self, *_args: Any) -> None:
        """切换分页方式：**这组参数重新填**（先清空、再套该形状的默认值），并按范围变更标脏。

        为什么整组清空而不是只清「不属于新形状」的键：`parameter` 这类键两种形状都有，
        但语义不同（页码参数名 vs 游标参数名）。若把「按页码」自动填的 `page` 带到游标形状，
        运行时会变成 `?page=<游标值>` —— 一个看起来正常、其实取错页的静默错误。
        换形状是明确动作，重填比「猜用户想复用哪个值」更可预测。
        """
        if self._updating or self._locked:
            return
        self._pagination_keep_original = self._pagination_combo.currentData() == _PAGINATION_KEEP
        shape = self._active_pagination_shape()
        for edit in self._pagination_edits.values():
            if not edit.text():
                continue
            edit.blockSignals(True)
            edit.setText("")
            edit.blockSignals(False)
        if shape is not None:
            for field in shape.fields:
                if not field.editable or field.default is None:
                    continue
                field_edit = self._pagination_edits.get(field.name)
                if field_edit is not None:
                    field_edit.blockSignals(True)
                    field_edit.setText(str(field.default))
                    field_edit.blockSignals(False)
        self._update_pagination_rows()
        self._on_scope_changed()

    def _load_pagination(self, pagination: Any) -> None:
        """把配置里的分页回填到控件。

        **契约外的键收进 `_pagination_extras`，写回时原样带出** —— 表单只拥有契约里
        `editable` 的那些字段（如 `location`、插件自带键都不属于它），否则就会出现
        「打开一个能用的配置、存一下就被删掉几个键」（本仓库已有过同类事故）。
        形状无法识别时进入「其它（保持原样）」：不猜、不改写。
        """
        loaded = dict(pagination) if isinstance(pagination, Mapping) else {}
        shape = detect_shape(loaded)
        self._pagination_extras = {
            key: value
            for key, value in loaded.items()
            if key != "type" and key not in _EDITABLE_PAGINATION_FIELDS
        }
        self._pagination_keep_original = bool(loaded) and shape is None
        target = _PAGINATION_KEEP if self._pagination_keep_original else (shape.key if shape else "")
        self._reset_pagination_choices()
        index = self._pagination_combo.findData(target)
        self._pagination_combo.setCurrentIndex(max(0, index))
        for name, edit in self._pagination_edits.items():
            value = loaded.get(name)
            edit.blockSignals(True)
            edit.setText("" if value is None else str(value))
            edit.blockSignals(False)
        self._update_pagination_rows()

    def _pagination_from_form(self) -> dict[str, Any] | None:
        """把分页控件读成 `source.pagination`；「不翻页」返回 ``None``。

        非法数字**不在这里悄悄丢弃**：原样写进配置，由 `core.pagination` 的校验器给出与
        CLI 相同的报错（否则用户会看到「配了却没生效」的假成功）。
        """
        shape = self._active_pagination_shape()
        if shape is None:
            return None
        values: dict[str, Any] = {"type": shape.key}
        for field in shape.fields:
            edit = self._pagination_edits.get(field.name)
            if not field.editable or edit is None:
                continue
            text = edit.text().strip()
            if not text:
                continue
            if field.kind == INTEGER:
                try:
                    values[field.name] = int(text)
                except ValueError:
                    values[field.name] = text
            else:
                values[field.name] = text
        merged = dict(self._pagination_extras)
        merged.update(values)
        return merged or None

    def _toggle_advanced(self) -> None:
        visible = not self._advanced_box.isVisible()
        self._advanced_box.setVisible(visible)
        self._advanced_btn.setText(_("高级设置 ▼") if visible else _("高级设置 ▶"))

    def _on_trial_pages_changed(self) -> None:
        self._trial_btn.setText(_("试跑 {0} 页").format(self._trial_pages_spin.value()))
