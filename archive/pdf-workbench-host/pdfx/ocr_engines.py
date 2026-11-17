"""转发壳：注册表实现已上提到 `omnicrawler.services.ocr_engines`。

保留本模块是为了**不破既有导入路径**（`pdfx.ocr` / `pdfx.config` 仍在用它们）。
解耦不是砍功能：行为与原来逐字一致，只是实现在宿主侧。

为什么搬家见 `services/ocr_engines.py` 的模块文档——
一句话：注册表描述"宿主支持哪些 OCR 引擎"，属支柱②，不该住在文档子系统里，
否则组件管理器会反向依赖 `pdfx`。
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..services.ocr_engines import (
    BUILTIN_ENGINES,
    engine_names_or_builtin,
    is_available,
    register_engine,
    registered_engines,
    resolve,
    unregister_for_tests,
)

__all__ = [
    "BUILTIN_ENGINES",
    "engine_names_or_builtin",
    "is_available",
    "register_engine",
    "registered_engines",
    "resolve",
    "unregister_for_tests",
]


def register_ocr_engine(
    name: str,
    factory: Callable[[dict[str, Any]], Any],
    *,
    available: Callable[[], bool] | None = None,
    replace: bool = False,
) -> None:
    """向后兼容别名（`pdfx.ocr` 历史上用的是这个名字）。"""
    register_engine(name, factory, available=available, replace=replace)
