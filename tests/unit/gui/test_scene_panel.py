"""S4：场景管理面板单元测试（offscreen 无头模式）。

覆盖：场景下拉刷新、槽位表填充、候选表填充、接受候选写回。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")


@pytest.fixture(scope="module")
def qt_app():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def test_scene_panel_loads_bundled_scenes(qt_app, tmp_path: Path) -> None:
    from omnicrawler.gui.views.scene_panel import ScenePanel

    panel = ScenePanel(tmp_path)
    panel.refresh_scenes()
    # 出厂场景 annual_report 应幂等导入
    assert panel._scene_combo.count() >= 1
    assert panel._current_scene() == "annual_report"


def test_scene_panel_slots_and_genes_populated(qt_app, tmp_path: Path) -> None:
    from omnicrawler.gui.views.scene_panel import ScenePanel

    panel = ScenePanel(tmp_path)
    panel.refresh_scenes()
    assert panel._slot_table.rowCount() >= 1  # annual_report 有 4 个槽位
    assert panel._slot_table.item(0, 0) is not None


def test_scene_panel_candidates_accept_roundtrip(qt_app, tmp_path: Path) -> None:
    from omnicrawler.gui.views.scene_panel import ScenePanel
    from omnicrawler.state.scene_store import SceneDocument, SceneStore

    # 先注入一条候选：创建文档指纹 + 候选
    store = SceneStore(tmp_path / "scene.sqlite3")
    store.import_bundled_scenes()
    slots = store.get_slots("annual_report")
    assert slots, "出厂场景应有槽位"
    doc_id = store.get_or_create_document(
        SceneDocument(document_hash="hash-abc", source_url="https://example.com/r")
    )
    slot_id = store.upsert_slot(slots[0])
    store.add_candidate(doc_id, slot_id, "星辰科技", confidence=0.9)
    store.close()

    panel = ScenePanel(tmp_path)
    panel.refresh_scenes()
    # 候选表应有 1 行（annual_report 场景，limit=200）
    assert panel._cand_table.rowCount() >= 1

    # 选中第 0 行并接受 → 状态应变为已验收
    panel._cand_table.selectRow(0)
    panel._accept_selected()
    assert panel._cand_table.item(0, 3).text() == "已验收"


def test_scene_panel_csv_export_escapes_formula(qt_app, tmp_path: Path, monkeypatch) -> None:
    """B10-001：CSV 导出必须 excel_safe 转义抽取值，防止 CWE-1236。"""
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from omnicrawler.gui.views.scene_panel import ScenePanel
    from omnicrawler.state.scene_store import SceneDocument, SceneStore

    store = SceneStore(tmp_path / "scene.sqlite3")
    store.import_bundled_scenes()
    slots = store.get_slots("annual_report")
    doc_id = store.get_or_create_document(
        SceneDocument(document_hash="hash-csv", source_url="=cmd|' /C calc'!A1")
    )
    slot_id = store.upsert_slot(slots[0])
    store.add_candidate(doc_id, slot_id, "=SUM(A1:A2)", confidence=0.9)
    store.close()

    panel = ScenePanel(tmp_path)
    panel.refresh_scenes()
    panel._cand_table.selectRow(0)
    panel._accept_selected()

    out = tmp_path / "accepted.csv"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", staticmethod(lambda *a, **k: (str(out), "CSV (*.csv)")))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    panel._export_accepted()
    content = out.read_text(encoding="utf-8")
    assert "'=SUM(A1:A2)" in content
    assert "'=cmd|' /C calc'!A1" in content
