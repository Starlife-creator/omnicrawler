"""TaskCanvas 的「③ 字段规则」域 —— 由 task_canvas.py 抽出的 Mixin（P1-3 第六批）。

涵盖字段规则区：
- ``_build_fields_area``：字段表格（渐进展示）+ 添加/删除/显示全部/启发式补全/视觉点选
- ``_add_field`` / ``_delete_field`` / ``_show_all_fields`` / ``_update_more_fields_btn``
- ``_heuristic_complete_fields``（本地兜底规则）/ ``_visual_pick`` / ``_apply_visual_candidates``
- ``_current_field_names`` / ``_append_field_row`` / ``_template_fields``

以 Mixin 形式保留 ``self`` 语义，宿主属性结构（五区 section 等）与调用点完全不变。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, cast

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableView,
)

from ..core.config_model import FieldDef
from ..i18n import _
from ..widgets.help_tooltip import HelpTooltip
from ..widgets.toast import ToastManager
from .task_canvas_components import FieldTableModel as _FieldTableModel
from .task_canvas_logic import GENERIC_FIELD_RULES as _GENERIC_FIELD_RULES
from .task_canvas_logic import selector_kind as _selector_kind

if TYPE_CHECKING:
    from PySide6.QtWidgets import QLineEdit, QWidget

    from ..core.config_model import CrawlConfig
    from .task_canvas_components import _Section

    # 类型检查期把宿主视作 QWidget（本域会以 self 作 _FieldTableModel 的 parent）；
    # 运行期仍为 object，故不引入 Qt 基类、不改变 TaskCanvas 的 MRO。
    _Base = QWidget
else:
    _Base = object


class FieldsAreaMixin(_Base):
    """③ 字段规则区构建与交互。"""

    # ---- 宿主契约：实例属性 ----
    _config: CrawlConfig
    _locked: bool
    _fields_section: _Section
    _fields_model: _FieldTableModel
    _fields_table: QTableView
    _url_edit: QLineEdit
    _complete_btn: QPushButton
    _more_fields_btn: QPushButton
    _visual_pick_btn: QPushButton
    _recommendation: Any | None

    if TYPE_CHECKING:
        # 由 TaskCanvas / 其他 Mixin 提供；仅类型检查期可见，运行期不存在，故不遮蔽宿主实现。
        def _on_field_changed(self, *_args: Any) -> None: ...

    # ------------------------------------------------------------------
    #  字段规则
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
        v_header = self._fields_table.verticalHeader()
        if v_header is not None:
            v_header.setVisible(False)
        header = self._fields_table.horizontalHeader()
        if header is not None:
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

            catalog = TemplateCatalog(package_resource("omnicrawler", "templates"))
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
                selector_type=cast(
                    Literal["css", "xpath", "jsonpath"], str(spec.get("type", "css"))
                ),
            ))
        return fields
