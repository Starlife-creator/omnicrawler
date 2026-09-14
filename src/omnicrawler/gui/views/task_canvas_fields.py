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
    QLineEdit,
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

        # 主按钮：**按真实页面结构**分析并填字段（2026-09-14 新增）。
        # 此前 GUI 完全用不到产品自带的 DOM 分析器（CLI 的 `analyze`），只能追加通用规则。
        self._analyze_btn = QPushButton(_("🔍 分析页面并填字段"))
        self._analyze_btn.setProperty("primary", True)
        self._analyze_btn.setToolTip(_("抓取入口网址并按页面结构推断列表容器与字段；只追加不覆盖"))
        self._analyze_btn.setAccessibleName(_("分析页面并填字段"))
        self._analyze_btn.setAccessibleDescription(
            _("需要先填写入口网址；抓取走与运行相同的守卫，失败会如实说明原因。")
        )
        self._analyze_btn.clicked.connect(self._analyze_page)
        # **默认禁用**：它需要先有网址。构建时不会走 `_sync_ui_state`（实测），
        # 所以不能依赖那里来置初值；由 `_update_analyze_button()` 在 URL/锁定态变化时驱动。
        self._analyze_btn.setEnabled(False)
        # P1：离线后备 —— 只按通用规则/模板追加（与目标页无关，故不再做主按钮）
        self._complete_btn = QPushButton(_("⚙️ 启发式补全字段"))
        self._complete_btn.setToolTip(
            _("不联网：按站点类型/模板补充常见字段；只去重追加，绝不覆盖你已有的字段")
        )
        self._complete_btn.clicked.connect(self._heuristic_complete_fields)
        # P4：视觉点选提升为主流程（专业/开发者可见，简单模式隐藏）
        self._visual_pick_btn = QPushButton(_("👆 视觉点选"))
        self._visual_pick_btn.setToolTip(_("打开可视化选字段：输入网址后点选目标元素生成字段；只追加不覆盖"))
        self._visual_pick_btn.clicked.connect(self._visual_pick)
        pick_row = QHBoxLayout()
        pick_row.addWidget(self._analyze_btn)
        # 帮助按钮必须真的绑上去：`test_help_ux` 断言"每个声明的帮助条目都挂在界面上"，
        # 只往 HELP_ENTRIES 里加条目而不绑控件会被它判红（本次实测被它抓到）。
        pick_row.addWidget(HelpTooltip("fields.analyze_page"))
        pick_row.addWidget(self._complete_btn)
        pick_row.addWidget(self._visual_pick_btn)
        pick_row.addStretch()
        body.addLayout(pick_row)

        # 列表项选择器（`extract.item_selector`）：2026-09-14 起 GUI 可**编辑**它。
        # 此前它是只保留不改写的 B 类透传键，于是"从零在表单里建不出列表任务"
        # ——整页会被当成一条记录，跑出来只有 1 条且字段错位。
        container_row = QHBoxLayout()
        container_row.addWidget(QLabel(_("列表项选择器")))
        self._item_selector_edit = QLineEdit()
        self._item_selector_edit.setObjectName("itemSelectorEdit")
        self._item_selector_edit.setPlaceholderText(_("例如 div.item；留空＝整页按一条记录"))
        self._item_selector_edit.setClearButtonEnabled(True)
        self._item_selector_edit.setAccessibleName(_("列表项选择器"))
        self._item_selector_edit.setAccessibleDescription(
            _("填写每条记录对应的容器选择器；留空表示整页按单个对象处理。")
        )
        self._item_selector_edit.textChanged.connect(self._on_item_selector_changed)
        container_row.addWidget(self._item_selector_edit, 1)
        container_row.addWidget(HelpTooltip("fields.item_selector"))
        body.addLayout(container_row)
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

    def _on_item_selector_changed(self, *_args: Any) -> None:
        """列表项选择器变更：同步配置 + 标 field 脏。

        与字段表同域：容器决定"一条记录"的边界，改了它却用旧试跑结果放行，
        等于让用户拿错配置去跑全量 —— `_mark_dirty(_DOMAIN_FIELD)` 会让试跑闸门失效。
        """
        if self._updating or self._locked:
            return
        self._sync_form_to_config()
        self._mark_dirty(self._DOMAIN_FIELD)

    def _analyze_page(self) -> None:
        """「分析页面并填字段」：抓当前网址 → 产品自带分析器 → 填容器与字段。

        与「启发式补全字段」的区别：后者只追加**通用**规则（`h1`/`a`/`.author`…，与目标页无关），
        本按钮按**真实页面结构**填，且**失败不假装成功** —— 明确报出原因，
        并给一个「改用通用规则」的去路（`ToastManager.error(..., action_text=...)`）。

        worker 以 `parent=self` 挂进视图树，因此主窗口关闭时的 `findChildren(QThread)`
        统一回收能覆盖它（与既有做法一致）。
        """
        if self._locked:
            return
        url = (self._url_edit.text() or "").strip()
        if not url:
            ToastManager.instance().info(_("请先填写入口网址，再点「分析页面并填字段」"))
            return
        self._analyze_btn.setEnabled(False)
        self._analyze_btn.setText(_("分析中…"))

        from ..core.workers import PageAnalyzeWorker

        http_section = self._config.passthrough.get("http")
        section = http_section if isinstance(http_section, dict) else {}
        worker = PageAnalyzeWorker(
            url,
            # 沿用任务自身的出网策略：用户在配置里放行了内网，分析就跟着放行；
            # 否则默认仍禁止访问本机/内网/保留地址（分析不比运行更宽松，也不更严格）。
            allow_private_network=bool(section.get("allow_private_network", False)),
            robots_fail_closed=bool(section.get("robots_fail_closed", True)),
            parent=self,
        )
        worker.succeeded.connect(lambda payload: self.apply_analysis(payload[0]))
        worker.failed.connect(self.set_analysis_failed)
        worker.finished.connect(worker.deleteLater)
        self._analyze_worker = worker
        worker.start()

    def _reset_analyze_button(self) -> None:
        """恢复分析按钮的文案与可用性（可用性由 URL 是否为空决定，见 `_sync_ui_state`）。"""
        self._analyze_btn.setText(_("🔍 分析页面并填字段"))
        self._update_analyze_button()

    def apply_analysis(self, report: dict[str, Any]) -> None:
        """把分析结果填进表单：**只补空、不覆盖**（与「启发式补全」同一 Upsert 语义）。"""
        self._reset_analyze_button()
        selector = str(report.get("item_selector") or "").strip()
        # 容器落在页面框架（导航/侧边栏/页脚）里 ⇒ 那不是业务列表。
        # 复用分析器/自动配置门禁的同一判据，**不填**并警告，避免把侧边栏当成列表。
        if bool(report.get("container_is_chrome")):
            ToastManager.instance().warning(
                _("只在页面框架（导航 / 侧边栏 / 页脚）里找到重复元素，未填入列表项选择器。"
                  "请确认入口网址就是列表页 —— 商品列表常在 /shop、/products 等路径下。")
            )
            selector = ""

        filled_container = False
        if selector and not self._item_selector_edit.text().strip():
            # 走控件（而非直接写配置）：textChanged 会带出同步 + 标脏 + 试跑失效
            self._item_selector_edit.setText(selector)
            filled_container = True

        existing = self._current_field_names()
        added = 0
        for field in report.get("fields") or []:
            name = str(field.get("name") or "").strip()
            field_selector = str(field.get("selector") or "").strip()
            if not name or not field_selector or name in existing:
                continue
            self._append_field_row(FieldDef(
                name=name,
                selector=field_selector,
                attribute=(str(field.get("attribute") or "") or None),
            ))
            existing.add(name)
            added += 1
        if added:
            self._on_field_changed()

        page_type = {
            "list": _("列表页"),
            "detail": _("详情页"),
            "gallery": _("图库页"),
            "search": _("搜索页"),
        }.get(str(report.get("page_type") or ""), _("结构未知"))
        parts = [_("按页面分析补入 {0} 个字段").format(added), page_type]
        if filled_container:
            parts.append(_("列表项选择器：{0}").format(selector))
        elif selector:
            parts.append(_("已保留你填的列表项选择器"))
        ToastManager.instance().success((" · ").join(parts))

    def set_analysis_failed(self, message: str) -> None:
        """分析失败：**如实报错**，并把「改用通用规则」作为显式去路（不静默降级）。"""
        self._reset_analyze_button()
        ToastManager.instance().error(
            _("页面分析失败：{0}").format(message),
            action_text=_("改用通用规则"),
            action_callback=self._heuristic_complete_fields,
        )

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
