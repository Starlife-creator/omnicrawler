"""缺可选依赖时必须**降级**而不是崩溃，且原因可见。

## 这条守卫防的是什么

插件声明 24 个第三方依赖，用户机器上必然有缺。两种情况都要保证：

1. **能降级**：缺 `pyarrow` 只让 `.parquet` 不可用，CSV/XLSX 照常；
2. **说原因**：自检列出缺什么、影响什么，而不是静默少功能。

`selfcheck()` 的清单已由 `test_plugin_selfcheck_deps.py` 保证"覆盖完整"；
本文件保证"覆盖了之后真的能答对"。

## 判据

用 `importlib.util.find_spec` 打桩模拟缺失（不真的卸载任何东西）：

* 缺失的包必须出现在 `optional_missing`，且带用途说明；
* 必需依赖（`required`）不受影响时 `ok=True`；
* 表格读写注册表：缺 `pyarrow` 则 `.parquet` 消失、`.csv` 仍在。

**为什么打桩 find_spec 而不是 `__import__`**：`selfcheck` 用的是
`importlib.util.find_spec`（为避开市场扫描器的"动态导入"硬拒规则），
所以必须在那层打桩才测得到。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_PLUGIN = (
    Path(__file__).resolve().parents[4]
    / "OmniCrawler-market"
    / "submissions"
    / "plugins"
    / "4c3014804d85de2568f85e62a3048429"
    / "document-workbench"
)


def _load_plugin():
    """导入插件的 `plugin.py`。

    **必须显式把插件根加入 sys.path**：`document_facade.pdf_views()` 确实会
    注入路径，但那只在它**首次加载**插件时发生；若同一进程里别的测试已经
    加载过（模块已缓存），路径注入就不会重跑，`import plugin` 会失败
    （实测：与 `test_plugin_gui_imports_execute.py` 同跑时报
    `ModuleNotFoundError: No module named 'plugin'`）。
    """
    _require_plugin()
    import sys

    from omnicrawler.services import document_facade

    document_facade.pdf_views()  # 兜底：确保插件根在 sys.path
    if str(_PLUGIN) not in sys.path:
        sys.path.insert(0, str(_PLUGIN))

    import plugin as plugin_module

    return plugin_module


def _require_plugin() -> None:
    from omnicrawler.services import document_facade

    if not document_facade.document_capability()["available"]:
        pytest.skip("需要 document-workbench 插件")


@pytest.fixture
def hide_modules(monkeypatch: pytest.MonkeyPatch):
    """返回一个函数：给定模块名列表，让 find_spec 对它们返回 None。"""

    def _hide(names: set[str]) -> None:
        real = importlib.util.find_spec

        def fake(name: str, *args: object, **kwargs: object):
            if name in names:
                return None
            return real(name, *args, **kwargs)

        monkeypatch.setattr(importlib.util, "find_spec", fake)

    return _hide


@pytest.mark.skipif(not _PLUGIN.is_dir(), reason="需要 market 仓插件目录")
def test_missing_optional_deps_are_reported_with_purpose(hide_modules) -> None:
    """缺可选依赖时必须列出**是哪几个**、**影响什么**。"""
    plugin_module = _load_plugin()
    hide_modules({"pyarrow", "duckdb", "openpyxl"})

    report = plugin_module.selfcheck()
    missing = {entry["module"]: entry["purpose"] for entry in report["optional_missing"]}

    for name in ("pyarrow", "duckdb", "openpyxl"):
        assert name in missing, f"{name} 缺失未被报告"
        assert missing[name].strip(), f"{name} 的报告没有用途说明"

    # 必需依赖都在 ⇒ ok 仍为 True（可选缺失不算失败）
    assert report["ok"] is True, (
        f"可选依赖缺失不该判 ok=False：{report['required_missing']}"
    )


@pytest.mark.skipif(not _PLUGIN.is_dir(), reason="需要 market 仓插件目录")
def test_missing_required_deps_sets_ok_false(hide_modules) -> None:
    """必需依赖缺失必须判 `ok=False`，且列出缺哪个。"""
    plugin_module = _load_plugin()
    hide_modules({"omnicrawler_docs.pdfx.config"})

    report = plugin_module.selfcheck()
    assert report["ok"] is False, "必需依赖缺失却判 ok=True"
    assert report["required_missing"], "没列出缺哪个必需依赖"
    assert any("pdfx.config" in item for item in report["required_missing"])


@pytest.mark.skipif(not _PLUGIN.is_dir(), reason="需要 market 仓插件目录")
def test_table_io_degrades_when_optional_backend_missing(hide_modules) -> None:
    """缺 `pyarrow` 只让 `.parquet` 消失，其余格式照常。"""
    _require_plugin()

    # **注册器用 `try: import pyarrow` 探测**（不是 find_spec），
    # 所以这里必须打桩 `__import__` / sys.modules，而不是上面那个 fixture。
    import builtins
    import io as _io
    import sys

    real_import = builtins.__import__

    def _blocked(name: str, *args: object, **kwargs: object):
        if name == "pyarrow" or name.startswith("pyarrow."):
            raise ImportError("模拟 pyarrow 缺失")
        return real_import(name, *args, **kwargs)

    sentinel = _io.StringIO()  # 占位，避免 linter 报未使用
    assert sentinel is not None

    # **必须先从 sys.modules 移除再导入**：`importlib.reload` 不会清空模块级的
    # `READERS` 字典，注册项会残留（实测踩到）。重新 import 才会在"缺 pyarrow"
    # 的环境下把注册表从零构建。
    import importlib

    saved_pyarrow = {
        k: sys.modules.pop(k) for k in list(sys.modules)
        if k == "pyarrow" or k.startswith("pyarrow.")
    }
    sys.modules.pop("omnicrawler.services.table_io", None)
    builtins.__import__ = _blocked
    try:
        table_io = importlib.import_module("omnicrawler.services.table_io")
    finally:
        builtins.__import__ = real_import

    assert ".parquet" not in table_io.READERS, "pyarrow 缺失却仍注册了 .parquet"
    assert ".csv" in table_io.READERS, "CSV 不该受 pyarrow 影响"
    assert ".jsonl" in table_io.READERS, "JSONL 不该受 pyarrow 影响"

    # 恢复现场：放回 pyarrow，重新导入让后续测试看到完整注册表
    sys.modules.update(saved_pyarrow)
    sys.modules.pop("omnicrawler.services.table_io", None)
    importlib.import_module("omnicrawler.services.table_io")
