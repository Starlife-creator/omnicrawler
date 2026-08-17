"""S4：场景管理面板 — 场景 / 槽位 / 选择器基因 / 候选验收 一览。

数据源：SceneStore（workspace/scene.sqlite3），懒加载避免 import 副作用；
默认场景（scenes/*.yaml 出厂快照）经 import_bundled_scenes 幂等导入。
基因反馈闭环可视化：各槽位最优基因的 fitness / hits / misses 直接展示。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import yaml
from PyQt6.QtCore import pyqtSlot
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.utils import excel_safe
from ..i18n import _
from ..widgets.empty_state import EmptyState


class ScenePanel(QWidget):
    """场景管理面板（侧栏导航项，NavIndex.SCENE=8；页面栈 index 11）。"""

    def __init__(self, workspace: Path, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._workspace = Path(workspace)
        self._store = None  # 懒加载
        self._build_ui()
        self.refresh_scenes()

    # ── 数据访问（懒加载）────────────────────────────────
    def _get_store(self):
        """懒加载 SceneStore（含幂等导入出厂场景）。"""
        if self._store is None:
            from ...state.scene_store import SceneStore

            store = SceneStore(self._workspace / "scene.sqlite3")
            store.import_bundled_scenes()
            self._store = store
        return self._store

    def _current_scene(self) -> str:
        return str(self._scene_combo.currentData() or "")

    # ── UI ────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(14)

        title = QLabel(_("\U0001f4dd 场景管理（Scene）"))
        title.setObjectName("pageTitle")
        subtitle = QLabel(_("场景 = 槽位定义 + 选择器基因。基因按适应度进化，候选可人工验收。"
                             "数据源：workspace/scene.sqlite3"))
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        scene_row = QHBoxLayout()
        scene_row.addWidget(QLabel(_("场景：")))
        self._scene_combo = QComboBox()
        self._scene_combo.currentIndexChanged.connect(self._on_scene_changed)
        scene_row.addWidget(self._scene_combo, 1)
        self._btn_refresh = QPushButton(_("刷新"))
        self._btn_refresh.clicked.connect(self.refresh_scenes)
        scene_row.addWidget(self._btn_refresh)
        root.addLayout(scene_row)

        # 空态提示：无任何场景数据时展示引导（默认隐藏）
        self._empty_state = EmptyState(
            icon="🗂",
            title=_("暂无场景数据"),
            description=_("点击右上角「刷新」从出厂快照导入默认场景（scenes/*.yaml），"
                          "或运行一次采集任务生成选择器基因。"),
            parent=self,
        )
        self._empty_state.hide()
        root.addWidget(self._empty_state)

        # 槽位定义表
        slot_box = QGroupBox(_("槽位定义"))
        self._slot_box = slot_box
        slot_layout = QVBoxLayout(slot_box)
        self._slot_table = QTableWidget(0, 6)
        self._slot_table.setHorizontalHeaderLabels([
            _("槽位"), _("名称"), _("抽取器"), _("模式/选择器"), _("值类型"), _("必填"),
        ])
        self._slot_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        slot_header = self._slot_table.horizontalHeader()
        if slot_header is not None:
            slot_header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._slot_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        slot_layout.addWidget(self._slot_table)
        slot_row = QHBoxLayout()
        slot_row.addStretch(1)
        self._btn_fields = QPushButton(_("生成为任务字段"))
        self._btn_fields.clicked.connect(self._export_slot_fields)
        slot_row.addWidget(self._btn_fields)
        slot_layout.addLayout(slot_row)
        root.addWidget(slot_box, 1)

        # 基因统计表
        gene_box = QGroupBox(_("选择器基因（按适应度推荐）"))
        self._gene_box = gene_box
        gene_layout = QVBoxLayout(gene_box)
        self._gene_table = QTableWidget(0, 6)
        self._gene_table.setHorizontalHeaderLabels([
            _("槽位"), _("选择器"), _("类型"), _("命中"), _("未中"), _("适应度"),
        ])
        self._gene_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        gene_header = self._gene_table.horizontalHeader()
        if gene_header is not None:
            gene_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        gene_layout.addWidget(self._gene_table)
        root.addWidget(gene_box, 1)

        # 候选验收
        cand_box = QGroupBox(_("抽取候选（待验收）"))
        self._cand_box = cand_box
        cand_layout = QVBoxLayout(cand_box)
        self._cand_table = QTableWidget(0, 5)
        self._cand_table.setHorizontalHeaderLabels([
            _("槽位"), _("值"), _("置信度"), _("状态"), _("时间"),
        ])
        self._cand_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        cand_header = self._cand_table.horizontalHeader()
        if cand_header is not None:
            cand_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        cand_layout.addWidget(self._cand_table)
        cand_row = QHBoxLayout()
        cand_row.addStretch(1)
        self._btn_export = QPushButton(_("导出已验收结果"))
        self._btn_export.clicked.connect(self._export_accepted)
        cand_row.addWidget(self._btn_export)
        self._btn_accept = QPushButton(_("接受所选候选"))
        self._btn_accept.clicked.connect(self._accept_selected)
        cand_row.addWidget(self._btn_accept)
        cand_layout.addLayout(cand_row)
        root.addWidget(cand_box, 1)

    # ── 行为 ──────────────────────────────────────────────
    @pyqtSlot()
    def refresh_scenes(self) -> None:
        """重载场景下拉 + 首场景内容。"""
        current = self._current_scene()
        try:
            store = self._get_store()
            scenes = store.list_scenes()
        except Exception:  # noqa: BLE001 — DB 不可用不阻断面板
            scenes = []
        # 空态切换：无任何场景时展示引导，隐藏表格组
        has_scenes = bool(scenes)
        self._empty_state.setVisible(not has_scenes)
        for box in (self._slot_box, self._gene_box, self._cand_box):
            box.setVisible(has_scenes)
        self._scene_combo.blockSignals(True)
        self._scene_combo.clear()
        if not has_scenes:
            self._scene_combo.addItem(_("（暂无场景，点击刷新导入出厂默认）"), "")
        for item in scenes:
            name = str(item.get("scene", ""))
            slot_count = int(item.get("slot_count", 0) or 0)
            gene_count = int(item.get("gene_count", 0) or 0)
            if name:
                self._scene_combo.addItem(
                    _("{0}（槽位 {1} / 基因 {2}）").format(name, slot_count, gene_count),
                    name,
                )
        self._scene_combo.blockSignals(False)
        if current:
            idx = self._scene_combo.findData(current)
            if idx >= 0:
                self._scene_combo.setCurrentIndex(idx)
        self._reload_scene_content()

    def _on_scene_changed(self) -> None:
        self._reload_scene_content()

    def _reload_scene_content(self) -> None:
        """按当前场景填充槽位表、基因表、候选表。"""
        scene = self._current_scene()
        self._slot_table.setRowCount(0)
        self._gene_table.setRowCount(0)
        self._cand_table.setRowCount(0)
        if not scene:
            return
        try:
            from ...services.gene_maintenance import scene_report

            store = self._get_store()
            report = scene_report(store, scene)
        except Exception:  # noqa: BLE001
            return

        slots = store.get_slots(scene)
        for slot in slots:
            row = self._slot_table.rowCount()
            self._slot_table.insertRow(row)
            self._slot_table.setItem(row, 0, QTableWidgetItem(slot.slot_key))
            self._slot_table.setItem(row, 1, QTableWidgetItem(slot.slot_name))
            self._slot_table.setItem(row, 2, QTableWidgetItem(slot.extractor_type))
            self._slot_table.setItem(row, 3, QTableWidgetItem(slot.pattern))
            self._slot_table.setItem(row, 4, QTableWidgetItem(slot.value_type))
            self._slot_table.setItem(row, 5, QTableWidgetItem(_("是") if slot.required else ""))

        slot_genes = report.get("slot_genes", {})
        for slot_key, genes in slot_genes.items():
            for gene in genes:
                row = self._gene_table.rowCount()
                self._gene_table.insertRow(row)
                self._gene_table.setItem(row, 0, QTableWidgetItem(slot_key))
                self._gene_table.setItem(row, 1, QTableWidgetItem(str(gene.get("selector", ""))))
                self._gene_table.setItem(row, 2, QTableWidgetItem(str(gene.get("selector_type", ""))))
                self._gene_table.setItem(row, 3, QTableWidgetItem(str(gene.get("hits", 0))))
                self._gene_table.setItem(row, 4, QTableWidgetItem(str(gene.get("misses", 0))))
                self._gene_table.setItem(row, 5, QTableWidgetItem(f'{float(gene.get("fitness", 0.0)):.2%}'))

        candidates = store.candidates(scene=scene, limit=200)
        for item in candidates:
            row = self._cand_table.rowCount()
            self._cand_table.insertRow(row)
            self._cand_table.setItem(row, 0, QTableWidgetItem(str(item.get("slot_key", ""))))
            self._cand_table.setItem(row, 1, QTableWidgetItem(str(item.get("value", ""))))
            conf = float(item.get("confidence", 0.0) or 0.0)
            self._cand_table.setItem(row, 2, QTableWidgetItem(f"{conf:.2f}"))
            self._cand_table.setItem(
                row, 3, QTableWidgetItem(_("已验收") if item.get("accepted") else _("待验收"))
            )
            self._cand_table.setItem(row, 4, QTableWidgetItem(str(item.get("created_at", ""))))
        self._btn_accept.setEnabled(bool(candidates))

    @pyqtSlot()
    def _accept_selected(self) -> None:
        """接受当前选中候选行（写回 SceneStore.accept_candidate）。"""
        row = self._cand_table.currentRow()
        if row < 0:
            return
        try:
            store = self._get_store()
            candidates = store.candidates(scene=self._current_scene(), limit=200)
            if row >= len(candidates):
                return
            candidate = candidates[row]
            store.accept_candidate(int(candidate["id"]))
        except Exception:  # noqa: BLE001
            return
        self._reload_scene_content()

    @pyqtSlot()
    def _export_accepted(self) -> None:
        """导出当前场景已验收候选（文档级透视 JSON/CSV，标准库零依赖）。"""
        scene = self._current_scene()
        if not scene:
            return
        try:
            store = self._get_store()
            rows = store.accepted_values(scene)
        except Exception:  # noqa: BLE001 — DB 不可用不阻断
            rows = []
        if not rows:
            QMessageBox.information(self, _("导出已验收结果"), _("当前场景暂无已验收候选。"))
            return

        path, _selected = QFileDialog.getSaveFileName(
            self,
            _("导出已验收结果"),
            str(Path.cwd() / f"{scene}_accepted.json"),
            _("JSON (*.json);;CSV (*.csv)"),
        )
        if not path:
            return
        try:
            suffix = Path(path).suffix.lower()
            if suffix == ".csv":
                headers = ["document_id", "source_url"]
                for key in sorted({k for row in rows for k in row if k not in headers}):
                    headers.append(key)
                with open(path, "w", encoding="utf-8", newline="") as fh:
                    writer = csv.DictWriter(fh, fieldnames=headers, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows({key: excel_safe(value) for key, value in row.items()} for row in rows)
            else:
                if not suffix:
                    path += ".json"
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump(rows, fh, ensure_ascii=False, indent=2)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, _("导出失败"), _("写入文件失败：{0}").format(exc))
            return
        QMessageBox.information(
            self, _("导出完成"),
            _("已导出 {0} 条文档的已验收结果到：\n{1}").format(len(rows), path),
        )

    @pyqtSlot()
    def _export_slot_fields(self) -> None:
        """场景槽位定义 → 任务字段配置（对齐模板 extract.fields 格式）。

        只导出不注入向导，避免破坏既有配置数据流；css/regex/jsonpath 直映射，
        text 槽位（包含匹配）无字段对应，跳过。
        """
        scene = self._current_scene()
        if not scene:
            return
        try:
            store = self._get_store()
            slots = store.get_slots(scene)
        except Exception:  # noqa: BLE001 — DB 不可用不阻断
            slots = []
        fields: dict[str, dict[str, str]] = {}
        skipped: list[str] = []
        for slot in slots:
            if slot.extractor_type == "text" or not slot.pattern:
                skipped.append(slot.slot_key)
                continue
            fields[slot.slot_key] = {"selector": slot.pattern, "type": slot.extractor_type}
        if not fields:
            QMessageBox.information(
                self, _("生成为任务字段"), _("当前场景没有可映射的槽位定义（css/regex/jsonpath）。")
            )
            return

        body = yaml.safe_dump({"fields": fields}, allow_unicode=True, sort_keys=False)
        text = (
            f"# Generated from scene '{scene}' slot definitions; "
            f"paste into task's extract.fields\n" + body
        )
        if skipped:
            text += f"# Skipped text-type slots without field mapping: {', '.join(skipped)}\n"

        path, _selected = QFileDialog.getSaveFileName(
            self,
            _("生成为任务字段"),
            str(Path.cwd() / f"{scene}_fields.yaml"),
            _("YAML (*.yaml)"),
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, _("导出失败"), _("写入文件失败：{0}").format(exc))
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        QMessageBox.information(
            self, _("已生成"),
            _("字段配置已保存到：\n{0}\n\n并已复制到剪贴板。").format(path),
        )
