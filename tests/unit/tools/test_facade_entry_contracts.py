"""facade 入口契约：每个公共入口必须真的可用（不是"能 import"）。

## 为什么需要这个守卫

2026-11-17 的迁移里，两处 facade 入口出现了同一类缺陷：

```python
# ① workbench.py
_pdf.require_document("pdfx.desktop").open_path(...)   # 插件模块没有 open_path

# ② _handlers.py ← convertx_module()
_convertx.OutputRule(...)                              # 插件模块没有 OutputRule
```

共同点：**`require_document()` 返回的是插件模块，而调用方以为它是一个
提供了具体符号的对象。** 症状是运行时 `AttributeError`，
"能 import"完全测不出来。

## 判据

对每个"返回对象"的 facade 入口，断言它返回的对象**具备调用方实际使用的属性**。
属性清单来自对宿主侧调用点（`workbench.py` / `_handlers.py` 等）的实际引用，
不是凭印象列的。

插件未安装时跳过——本测试验证的是"装了之后拿到的东西对不对"。
"""

from __future__ import annotations

import pytest

from omnicrawler.services import document_facade


def _require_plugin() -> None:
    if not document_facade.document_capability()["available"]:
        pytest.skip("需要 pdf-workbench 插件")


def test_convertx_module_exposes_output_rule() -> None:
    """`convertx_module()` 必须暴露 `OutputRule` 与 `convert`。

    `cli/_handlers.py` 用 `_convertx.OutputRule(**rule_kwargs)` 构造输出规则。
    直接返回插件模块会让这条路径 `AttributeError`。
    """
    _require_plugin()
    module = document_facade.convertx_module()
    assert hasattr(module, "OutputRule"), (
        "convertx_module() 返回的对象没有 OutputRule —— "
        "`omnicrawler convert` 带输出规则时会 AttributeError"
    )
    assert hasattr(module, "convert"), "convertx_module() 返回的对象没有 convert"
    # 能真的实例化，不是仅有个同名属性
    assert module.OutputRule() is not None


def test_table_io_exposes_registries() -> None:
    """`table_io()` 必须带 `READERS` / `WRITERS` / `sniff_format`（`transform` 命令需要）。"""
    _require_plugin()
    table_io = document_facade.table_io()
    for name in ("READERS", "WRITERS", "sniff_format"):
        assert hasattr(table_io, name), f"table_io() 缺 {name}"
    assert table_io.READERS, "READERS 为空——没有任何格式可读"
    assert callable(table_io.sniff_format)


def test_pdf_views_exposes_both_view_classes() -> None:
    """`pdf_views()` 必须带两个视图类（`central_area` 按名取用）。"""
    _require_plugin()
    views = document_facade.pdf_views()
    for name in ("PdfWorkbenchView", "ConvertView"):
        assert hasattr(views, name), f"pdf_views() 缺 {name}"


def test_class_returning_entries_return_classes() -> None:
    """返回**类**的入口不能返回实例或模块。

    调用方要按每次请求的配置构造后端/对话框，拿到实例就复用错了。
    """
    _require_plugin()
    import inspect

    for name in ("ocr_backend_class", "document_block_class", "pdf_region_selector"):
        value = getattr(document_facade, name)()
        assert inspect.isclass(value), f"{name}() 返回的不是类：{type(value).__name__}"


def test_public_entries_are_callable() -> None:
    """facade 的公共函数都必须可调用（不是 None / 不是模块）。"""
    import inspect

    for name, value in inspect.getmembers(document_facade):
        if name.startswith("_"):
            continue
        if not inspect.isfunction(value):
            continue
        if value.__module__ != document_facade.__name__:
            continue
        assert callable(value), f"{name} 不可调用"
