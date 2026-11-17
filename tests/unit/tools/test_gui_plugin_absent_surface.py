"""GUI 层：插件未安装时不得出现「点了才知没有」的入口与崩溃。

## 这条守卫防的三件事

2026-11-23 实测，代码迁成插件后 GUI 层留下三处问题：

| 问题 | 症状 |
|---|---|
| `capabilities.navigation_capabilities()["pdf"]` | **只探测 `pdfplumber`**（宿主的一个 Python 包），不检查插件 ⇒ 装了 pdfplumber 的机器**显示 PDF 入口**，点进去是"插件未安装" |
| `MainWindow._open_pdf_workbench` | 未装插件时 `self._pdf_workbench is None`，直接调它的方法 ⇒ `AttributeError` |
| `central_area` 的格式互转信号 | 用 `NavIndex.CONVERT_TOOL`（**行号**），未装插件时该行指向 `login_session` ⇒ 点"格式互转"跳到"登录会话" |

三者都是"用户点下去才知没有"，正是要消除的形态。

## 判据

1. `pdf` 能力必须**以插件安装状态为准**，不能只看 pdfplumber；
2. `_open_pdf_workbench` 在视图为 None 时必须给提示 + 导向插件市场，不抛异常；
3. 格式互转跳转必须**按 key 定位**，页面不存在时提示而不是跳到错页。
"""

from __future__ import annotations

import pytest

from omnicrawler.core import capabilities
from omnicrawler.core.capabilities import navigation_capabilities


def test_pdf_capability_requires_plugin_not_just_pdfplumber(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`pdf` 能力必须以**插件安装状态**为准。

    仅仅有 pdfplumber 不算——那只是宿主的一个 Python 包，
    不代表 PDF 工作台可用。
    """
    from omnicrawler.services import document_facade

    # 场景 A：插件未装（但 pdfplumber 装了）
    monkeypatch.setattr(
        document_facade,
        "document_capability",
        lambda: {"available": False, "reason": "测试桩", "missing": [], "plugin_installed": False},
    )
    monkeypatch.setattr(
        capabilities.importlib.util, "find_spec",
        lambda name: object() if name == "pdfplumber" else None,
    )
    assert navigation_capabilities()["pdf"] is False, (
        "插件未装却显示 PDF 入口 —— 用户点进去只会看到\u201c插件未安装\u201d"
    )

    # 场景 B：插件已装（且 pdfplumber 也有）
    monkeypatch.setattr(
        document_facade,
        "document_capability",
        lambda: {"available": True, "reason": "", "missing": [], "plugin_installed": True},
    )
    assert navigation_capabilities()["pdf"] is True


def test_open_pdf_workbench_handles_missing_view(monkeypatch: pytest.MonkeyPatch) -> None:
    """视图为 None 时不得抛 AttributeError，要给提示并导向插件市场。"""
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication, QMessageBox

    app = QApplication.instance() or QApplication([])
    assert app is not None

    from omnicrawler.gui import main as gui_main
    from omnicrawler.gui import navigation

    shown: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda *a, **k: shown.append(str(a[1]) if len(a) > 1 else "")),
    )
    visited: list[str] = []
    monkeypatch.setattr(
        gui_main.navigation_host, "goto_nav",
        lambda _window, key: visited.append(key),
    )

    class _FakeWindow:
        _pdf_workbench = None  # 未装插件时的真实状态

    gui_main.MainWindow._open_pdf_workbench(_FakeWindow(), "测试请求")

    assert shown, "没有给用户任何提示"
    assert navigation.NavKey.PLUGIN_MARKET in visited, (
        "没有导向插件市场——用户不知道去哪里装"
    )


def test_convert_tool_navigation_uses_key_not_row_index() -> None:
    """格式互转跳转必须按 key 定位，不能按行号。

    `NavIndex.CONVERT_TOOL` 是行号，随导航项增删而变。未装插件时该行
    实际指向 `login_session`（实测），点"格式互转"会跳到"登录会话"。
    """
    from omnicrawler.gui.delegates import central_area

    source = __import__("inspect").getsource(central_area._goto_nav_key)
    assert "visible_entries" in source, (
        "_goto_nav_key 没有按 key 查表——可能退回了行号定位"
    )
    assert "keys.index(nav_key)" in source, (
        "没有按 key 求行号，可能仍在用 NavIndex"
    )

    # 并且调用点不能再用 NavIndex.CONVERT_TOOL
    module_source = __import__("inspect").getsource(central_area)
    connect_region = module_source.split("open_convert_tool.connect", 1)
    assert len(connect_region) == 2, "没找到 open_convert_tool 的连接点"
    region = connect_region[1][:400]
    assert "NavIndex.CONVERT_TOOL" not in region, (
        "格式互转仍用 NavIndex（行号）跳转——未装插件时会跳到错误页面"
    )
    assert "NavKey.CONVERT_TOOL" in region, "没有用 NavKey 定位"


def test_capability_report_separates_pdf_text_from_pdf_workbench(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """能力报告必须**分开**报「PDF 文字层」与「PDF 工作台」。

    早先只有一条 `pdf_text`，描述写着"提取 PDF 文字、表格和元数据"，
    而判据只是 `pdfplumber` 是否安装——装了 pdfplumber 就显示"就绪"，
    但表格/元数据其实要插件才有。

    现在拆成两条：`pdf_text`（宿主内置文字层）+ `pdf_workbench`（插件）。
    用户一眼能看出"缺的是插件，不是 Python 包"。
    """
    from omnicrawler.core.capabilities import capability_report
    from omnicrawler.services import document_facade

    monkeypatch.setattr(
        document_facade,
        "document_capability",
        lambda: {"available": False, "reason": "测试桩", "missing": [], "plugin_installed": False},
    )
    items = capability_report(mode="quick")["standard"]["items"]

    assert "pdf_text" in items, "缺少 PDF 文字层能力项"
    assert "pdf_workbench" in items, (
        "缺少独立的 PDF 工作台能力项——用户会把「有 pdfplumber」误读成「工作台可用」"
    )
    assert items["pdf_workbench"]["ready"] is False, "插件未装却报工作台就绪"
    description = str(items["pdf_workbench"].get("description", ""))
    assert "插件" in description, f"工作台条目的说明没提到插件：{description!r}"

    # 插件的缺失原因也要落在文字层那条里，避免用户以为文字层也没了
    text_description = str(items["pdf_text"].get("description", ""))
    assert "工作台" in text_description, (
        f"文字层条目没提示\u201c表格与元数据需插件\u201d：{text_description!r}"
    )
