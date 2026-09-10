"""TaskCanvas 的「场景 / 模板推荐」域 —— 由 task_canvas.py 抽出的 Mixin（P1-3 第二批）。

涵盖两块功能：
- N4：选用场景（懒加载 SceneStore）→ 槽位生成字段 + 写 extract.scene
- P1：模板推荐闸门前移（本地 L1/L2，零网络；L3 嗅探默认关闭不注入 fetcher）

以 Mixin 形式保留 ``self`` 语义，因此 TaskCanvas 的属性结构（五区 section、
persistent_action_bar 等）与调用点完全不变。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from ..core.config_model import FieldDef
from ..i18n import _
from ..widgets.toast import ToastManager

if TYPE_CHECKING:
    from PySide6.QtWidgets import QComboBox, QLabel, QPushButton

    from ..core.config_model import CrawlConfig
    from .task_canvas_components import FieldTableModel as _FieldTableModel

LOGGER = logging.getLogger(__name__)


class SceneRecommendationMixin:
    """场景选择与模板推荐的槽位/闸门逻辑。

    本 Mixin 依赖宿主 ``TaskCanvas`` 提供的属性与方法（见下方契约声明），
    因此只声明类型、不持有实现，也不引入任何 Qt 基类。
    """

    # ---- 宿主契约（TaskCanvas 在 __init__/各 _build_* 中创建）----
    _config: CrawlConfig
    _project_root: str | None
    _updating: bool
    _locked: bool
    _source_badge: QLabel
    _rec_label: QLabel
    _template_combo: QComboBox
    _scene_combo: QComboBox
    _ignore_rec_btn: QPushButton
    _reject_btn: QPushButton
    _fields_model: _FieldTableModel

    if TYPE_CHECKING:
        # 由 TaskCanvas 提供；仅在类型检查期声明，运行期不存在，故不会遮蔽宿主实现。
        def _on_scope_changed(self, *_args: Any) -> None: ...
        def _on_field_changed(self, *_args: Any) -> None: ...

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
                                cast(Literal["css", "xpath", "jsonpath"], slot.extractor_type)
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
            from omnicrawler.core import categorizer as _cat_mod
            from omnicrawler.core.categorizer import (
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
        from PySide6.QtWidgets import QMenu

        from ...quality.template_feedback import REJECT_LABELS

        menu = QMenu(self._reject_btn)
        for label in REJECT_LABELS:
            # 带默认参 lambda 供 mypy 推断失败：显式标注参数类型
            def _reject(lbl: str = label) -> None:
                self._record_rejection(lbl)

            menu.addAction(label, _reject)
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
            store = TemplateFeedbackStore(
                root / "workspace" / "logs" / "template_feedback.jsonl", root=root
            )
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
