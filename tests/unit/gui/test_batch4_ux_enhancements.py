"""Offscreen GUI & unit tests for Batch 4 UX & in-context capability switching."""

from __future__ import annotations

import csv
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("PySide6") is None,
    reason="GUI UX tests require PySide6",
)

_APP: object | None = None


@pytest.fixture(autouse=True)
def _qt_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from omnicrawler.gui.command_palette import (
        clear_plugin_palette_commands,
        clear_recent_palette_commands,
    )
    from omnicrawler.gui.settings import AppSettings

    global _APP
    _APP = QApplication.instance() or QApplication([])
    assert _APP is not None
    monkeypatch.setattr(
        "omnicrawler.gui.settings.make_qsettings",
        lambda *_args: QSettings(
            str(tmp_path / "settings.ini"), QSettings.Format.IniFormat
        ),
    )
    AppSettings.reset()
    clear_plugin_palette_commands()
    clear_recent_palette_commands()
    yield _APP
    clear_plugin_palette_commands()
    clear_recent_palette_commands()
    AppSettings.reset()


def test_task_canvas_in_context_capability_switching_and_unknown_plugin_preservation() -> None:
    from omnicrawler.gui.core.config_model import CrawlConfig
    from omnicrawler.gui.views.task_canvas import TaskCanvas
    cfg = CrawlConfig()
    cfg.passthrough["browser"] = {"engine": "my-custom-browser-plugin"}
    cfg.passthrough["extract"] = {"parser": "selectolax"}

    canvas = TaskCanvas(CrawlConfig())
    canvas.load_config(cfg)
    # 未知第三方浏览器插件值必须原样保留在下拉框并选中，绝不静默篡改
    assert canvas._browser_engine_combo.currentData() == "my-custom-browser-plugin"
    assert canvas._html_parser_combo.currentData() == "selectolax"

    # 就地切换为 selenium 与 lxml，自动同步回配置
    sel_idx = canvas._browser_engine_combo.findData("selenium")
    lxml_idx = canvas._html_parser_combo.findData("lxml")
    assert sel_idx >= 0 and lxml_idx >= 0
    canvas._browser_engine_combo.setCurrentIndex(sel_idx)
    canvas._html_parser_combo.setCurrentIndex(lxml_idx)

    updated = canvas.get_config()
    assert updated.passthrough["browser"]["engine"] == "selenium"
    assert updated.passthrough["extract"]["parser"] == "lxml"


def test_task_canvas_optional_inspector_previews_and_appends_without_overwriting() -> None:
    from omnicrawler.gui.core.config_model import CrawlConfig, FieldDef
    from omnicrawler.gui.views.task_canvas import TaskCanvas

    cfg = CrawlConfig()
    cfg.fields = [FieldDef(name="标题", selector="h2.custom-keep", selector_type="css")]
    canvas = TaskCanvas(CrawlConfig())
    canvas.load_config(cfg)

    # 默认收起；点击按钮按需展开；切到简单模式自动隐藏
    assert not canvas._inspector_panel.isVisible()
    canvas._inspector_toggle_btn.setChecked(True)
    assert canvas._inspector_panel.isVisibleTo(canvas)
    canvas.set_simple_mode(True)
    assert not canvas._inspector_panel.isVisibleTo(canvas)
    canvas.set_simple_mode(False)
    assert canvas._inspector_panel.isVisibleTo(canvas)

    sample_html = """<html><body><div class="list">
      <div class="card"><h2 class="title">A</h2><span class="price">¥10</span></div>
      <div class="card"><h2 class="title">B</h2><span class="price">¥20</span></div>
      <div class="card"><h2 class="title">C</h2><span class="price">¥30</span></div>
    </div></body></html>"""
    report = canvas._inspector_panel.inspect_html(sample_html, canvas.get_config())
    assert report["best_selector"]
    assert report["inferred_fields"]

    # 点击「追加推断字段」：已有「标题」选择器必须原样保留，新字段（如价格）被追加
    canvas._inspector_panel.apply_candidates_btn.click()
    by_name = {f.name: f.selector for f in canvas.get_config().fields}
    assert by_name["标题"] == "h2.custom-keep"
    assert "价格" in by_name
    assert canvas.get_config().item_selector() == report["best_selector"]


def test_command_palette_fuzzy_search_mru_and_plugin_injection() -> None:
    from omnicrawler.gui.command_palette import (
        PaletteCommand,
        build_palette_commands,
        filter_palette_commands,
        record_recent_palette_command,
        register_plugin_palette_command,
        unregister_plugin_palette_command,
    )

    triggered: list[str] = []
    ext = PaletteCommand(
        command_id="plugin:ocr-bench",
        title="运行 OCR 质量巡检",
        category="扩展动作",
        keywords=("ocr", "benchmark"),
        shortcut="Ctrl+Alt+O",
        callback=lambda: triggered.append("ocr"),
    )
    register_plugin_palette_command(ext)
    stub = SimpleNamespace(_settings=SimpleNamespace(theme="light", shortcuts={}))
    cmds = build_palette_commands(stub, capabilities={"pdf": False, "qml": False})
    assert any(c.command_id == "plugin:ocr-bench" for c in cmds)

    # FZF 子序列模糊搜索："ocrbn" 命中 "plugin:ocr-bench"
    fuzzy_hits = filter_palette_commands(cmds, "ocrbn")
    assert fuzzy_hits and fuzzy_hits[0].command_id == "plugin:ocr-bench"

    # MRU 最近使用排序：空查询时最近执行项排到首位
    record_recent_palette_command("plugin:ocr-bench")
    mru_sorted = filter_palette_commands(cmds, "")
    assert mru_sorted[0].command_id == "plugin:ocr-bench"

    assert unregister_plugin_palette_command("plugin:ocr-bench") is True


def test_result_table_column_hide_guard_and_selected_rows_export(tmp_path: Path) -> None:
    from PySide6.QtCore import QItemSelectionModel

    from omnicrawler.gui.views.result_table import ResultTable

    csv_file = tmp_path / "records.csv"
    with csv_file.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["title", "price", "secret_col"])
        writer.writerow(["=1+1", "10", "x"])
        writer.writerow(["Item B", "20", "y"])
        writer.writerow(["Item C", "20", "z"])

    view = ResultTable()
    assert view._model.load_file(csv_file) is True
    view._update_pagination()

    # 隐藏 secret_col 成功；但尝试把最后仅剩的列也隐藏必须被拒绝（至少留 1 列）
    assert view.set_column_hidden(2, True) is True
    assert view.set_column_hidden(0, True) is True
    assert view.set_column_hidden(1, True) is False  # 不能把所有列全藏光
    view.show_all_columns()
    assert view.set_column_hidden(2, True) is True  # 只藏第 3 列以验证导出只含可见列

    # 选中前两行并导出（验证公式注入防护与列可见性过滤）
    sel = view._table.selectionModel()
    assert sel is not None
    sel.select(
        view._proxy.index(0, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    sel.select(
        view._proxy.index(1, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows,
    )
    out_csv = tmp_path / "selected.csv"
    exported = view.export_selected_rows_csv(out_csv)
    assert exported == 2
    rows = list(csv.reader(out_csv.read_text(encoding="utf-8-sig").splitlines()))
    assert rows[0] == ["title", "price"]
    assert rows[1][0].startswith("'")  # excel_safe 已阻断 =1+1 公式注入
    assert rows[2] == ["Item B", "20"]
