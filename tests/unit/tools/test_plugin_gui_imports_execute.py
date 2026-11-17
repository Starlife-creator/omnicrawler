"""插件 GUI 视图的**函数内导入**必须真的能执行。

## 这条守卫补齐的缺口

`test_plugin_no_stale_host_imports.py` 用**静态扫描**找失效引用，
但它只检查字面文本——如果某人把 `from omnicrawler.pdfx` 改成
`from ..pdfx`，静态扫描就绿了，**但目标模块是否真有那个符号**并没验证。

2026-11-22 的实测说明了这条的必要：4 处失效导入全在函数内部，
导入期不报错、471 个测试全绿，而用户点开界面就 `ModuleNotFoundError`。

## 判据

对每个"函数内导入点"，**真的执行一次导入**（不是正则匹配），
并断言符号存在。覆盖四处曾经出事的位置：

| 文件 | 导入 |
|---|---|
| `convert_tool` | `OutputRule` / `resolve_output_path` / `format_status` |
| `document_workbench_worker` | `run_extraction` |
| `document_workbench` | `create_project_config` |

另有一条行为级测试：真的触发 `ConvertView._auto_suggest_dst()`，
走完整回调（构造视图 → 设源文件 → 触发），验证路径建议能产出。
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from omnicrawler.services import document_facade


def _require_plugin() -> None:
    if not document_facade.document_capability()["available"]:
        pytest.skip("需要 document-workbench 插件")


def _plugin_modules():
    """载入插件视图模块（`pdf_views()` 会把插件目录加入 sys.path）。"""
    _require_plugin()
    document_facade.pdf_views()
    from omnicrawler_docs.gui_views import convert_tool, document_workbench, document_workbench_worker

    return convert_tool, document_workbench, document_workbench_worker


def test_convert_tool_imports_resolve() -> None:
    """`convert_tool` 里用到的转换符号必须存在。"""
    _require_plugin()
    document_facade.pdf_views()

    from omnicrawler_docs.convertx import OutputRule, resolve_output_path
    from omnicrawler_docs.convertx.catalog import format_status

    assert inspect.isclass(OutputRule)
    assert callable(resolve_output_path)
    assert callable(format_status)


def test_worker_imports_resolve() -> None:
    """`document_workbench_worker` 的 `run_extraction` 必须存在。"""
    _require_plugin()
    document_facade.pdf_views()

    from omnicrawler_docs.pdfx.service import run_extraction

    assert callable(run_extraction)


def test_project_config_import_resolves() -> None:
    """`document_workbench` 的 `create_project_config` 必须存在。"""
    _require_plugin()
    document_facade.pdf_views()

    from omnicrawler_docs.pdfx.project import create_project_config

    assert callable(create_project_config)


def test_view_source_uses_relative_imports_only() -> None:
    """三个视图模块里不得再有 `omnicrawler.pdfx|convertx` 绝对导入。

    与静态扫描守卫互补：这条只看这三处曾经出事的位置，
    给出更直接的定位信息。
    """
    convert_tool, document_workbench, document_workbench_worker = _plugin_modules()
    broken_prefixes = (
        "omnicrawler.pdfx",
        "omnicrawler.convertx",
        "omnicrawler.document_ir",
        "omnicrawler.apps",
    )
    problems: list[str] = []
    for module in (convert_tool, document_workbench, document_workbench_worker):
        source = inspect.getsource(module)
        for number, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            if not (stripped.startswith("from ") or stripped.startswith("import ")):
                continue
            if any(prefix in stripped for prefix in broken_prefixes):
                problems.append(f"{module.__name__}:{number}  {stripped}")
    assert not problems, "视图模块仍有指向已迁出包的导入:\n  " + "\n  ".join(problems)


def test_convert_view_auto_suggest_runs_end_to_end(tmp_path: Path) -> None:
    """真的触发 `_auto_suggest_dst()`——曾因失效导入而崩溃的路径。

    这条走完整流程：构造视图 → 设源文件 → 触发回调 → 检查建议结果。
    仅断言"导入能通过"不够，因为该导入在函数深处，只有真的调用
    才会执行到。
    """
    _require_plugin()

    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    assert app is not None

    convert_tool, _, _ = _plugin_modules()
    view = convert_tool.ConvertView()

    source = tmp_path / "sample.csv"
    source.write_text("a,b\n1,2\n", encoding="utf-8")
    view._src_path = source

    # 修复前这里会 ModuleNotFoundError（omnicrawler.convertx 已不在宿主）
    view._auto_suggest_dst()

    suggested = view._dst_path_edit.text()
    assert suggested, "没有给出建议输出路径"
    assert suggested.endswith(".jsonl") or suggested.endswith(".csv"), (
        f"建议路径后缀异常：{suggested}"
    )
