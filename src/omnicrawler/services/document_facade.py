"""文档子系统的**唯一**宿主侧入口（第 2 批：挂点收口）。

为什么要有这个 facade
---------------------
文档工作台（`pdfx` / `convertx` / `apps` / `document_ir` / `review` / `export`）
要从"核心功能"降为**可选组件**。但宿主里有 8 处真实挂点需要使用它：

```
cli/_main.py                     PDF 命令行转发
cli/_handlers.py                 格式转换（convertx）+ 运行对比（review）
services/workbench.py            PDF 工作台的全部后端调用
pipeline_ops/pdf_integration.py  PDF 任务编排
pipeline_ops/preflight.py        PDF 预检
```

若各处直接 import，组件化时要逐处去改，**漏一处就是运行期 ImportError**。
收口到本模块后，只需改这一处。

两条硬约束
----------
1. **导入本模块不得拉起文档子系统**。这是"可选"的前提——否则"装不装都能启动"
   就是假的。所以全部符号都在**函数内**导入。
2. **缺组件时必须给出可操作提示**，而不是裸 ImportError。
   用户看到的应该是"需要安装 PDF 工作台组件"，而不是一段栈。

能力探测
--------
:func:`document_capability` 回答"当前环境有没有文档能力"，
供导航门禁（未装不显示入口）与诊断使用。
:func:`require_document` 在真正要用时取子模块，缺失时抛**带说明**的错误。

注意：本模块**不做**能力降级或功能替代——它只是入口与诊断。
真正的"没装就不显示"由 GUI 导航层按 `document_capability()` 决定。
"""
from __future__ import annotations

import importlib
from typing import Any

#: 文档子系统涉及的顶层包（用于能力探测与诊断报告）。
DOCUMENT_PACKAGES: tuple[str, ...] = (
    "pdfx",
    "convertx",
    "apps",
    "document_ir",
    "doc_extractors",
    "review",
    "export",
)

#: 判断"文档能力是否可用"的最小充分集合。
#:
#: 用 `pdfx.service` 而不是 `pdfx` 本身：`pdfx/__init__` 可能很薄，
#: 真正能干活的是 service/config 这些。缺任何一个都不算可用。
_REQUIRED_MODULES: tuple[str, ...] = ("pdfx.service", "pdfx.config", "pdfx.project")


def require_document(module: str) -> Any:
    """取一个文档子系统模块；缺失时抛**可操作**的错误。

    :param module: 形如 ``"pdfx.service"`` 的点分模块名（不含 ``omnicrawler.`` 前缀）。
    """
    name = str(module or "").strip()
    if not name:
        raise ValueError("require_document 需要模块名")
    try:
        return importlib.import_module(f"omnicrawler.{name}")
    except ImportError as exc:
        raise ImportError(
            f"文档工作台组件不可用：缺少模块 omnicrawler.{name}。\n"
            "该功能已改为可选组件——请在「插件市场 → 组件」中安装 PDF 工作台组件，"
            "或改用文字版流程（爬虫与 AI 主流程不受影响）。\n"
            f"（原始错误：{exc}）"
        ) from exc


def document_capability() -> dict[str, Any]:
    """报告文档能力是否可用，以及**为什么不可用**。

    返回::

        {"available": bool, "reason": str, "missing": [模块名...]}

    * 可用时 ``reason`` 为空串；
    * 不可用时 ``missing`` 列出缺失的必需模块，``reason`` 是给用户看的一句话。

    ⚠ **探测只看文件是否存在，绝不 import**。
    第一版用 ``importlib.import_module`` 检查，一调用就把整个 `pdfx`
    （含 OCR、parser、LLM）拉进 `sys.modules`——那样"可选组件"是假的：
    光问一句"你有没有"就已经加载全部。
    第二版改用 ``importlib.util.find_spec``，**它仍会导入父包**
    （实测 `omnicrawler.pdfx` 照样进 `sys.modules`）。
    所以这里直接按包目录下的文件存在性判断，代价恒定且零导入。
    """
    from pathlib import Path as _Path

    root = _Path(__file__).resolve().parent.parent  # omnicrawler/
    missing: list[str] = []
    for module in _REQUIRED_MODULES:
        relative = module.replace(".", "/")
        # 模块可能是单文件，也可能是包（目录 + __init__.py）
        if not (
            (root / f"{relative}.py").is_file()
            or (root / relative / "__init__.py").is_file()
        ):
            missing.append(module)
    if missing:
        return {
            "available": False,
            "reason": (
                "文档工作台组件未安装（缺少 "
                + "、".join(missing)
                + "）；可在「插件市场 → 组件」中安装，或使用文字版流程。"
            ),
            "missing": missing,
        }
    return {"available": True, "reason": "", "missing": []}


# ── 便捷入口：挂点应当只用这些，不要直接 import 文档子系统 ──────────────
#
# 全部在函数内导入，保证"导入本模块不会拉起 pdfx/convertx"。


def load_pdf_config(path: Any) -> Any:
    """加载 PDF 项目配置（带 schema 校验）。"""
    return require_document("pdfx.config").load_config(path)


def open_pdf_database(path: Any) -> Any:
    """打开 PDF 项目数据库。"""
    return require_document("pdfx.database").Database(path)


def create_project_config(*args: Any, **kwargs: Any) -> Any:
    """创建 PDF 项目配置。"""
    return require_document("pdfx.project").create_project_config(*args, **kwargs)


def run_pdf_extraction(*args: Any, **kwargs: Any) -> Any:
    """运行 PDF 抽取。"""
    return require_document("pdfx.service").run_extraction(*args, **kwargs)


def run_pdf_processing(*args: Any, **kwargs: Any) -> Any:
    """运行 PDF 处理。"""
    return require_document("pdfx.service").run_processing(*args, **kwargs)


def pdf_database_status(*args: Any, **kwargs: Any) -> Any:
    """查询 PDF 项目数据库状态。"""
    return require_document("pdfx.service").database_status(*args, **kwargs)


def apply_pdf_review(*args: Any, **kwargs: Any) -> Any:
    """应用人工复核决定。"""
    return require_document("pdfx.review").apply_review(*args, **kwargs)


def pdf_main(argv: list[str] | None = None) -> Any:
    """转发到 PDF 子命令的 main。

    ⚠ ``pdfx.cli.main()`` 的历史签名**不接受参数**（它读 ``sys.argv``），
    而 ``apps.*.main()`` 接受 ``argv``。这里如实反映差异，不做统一——
    强行统一会改变调用方的 sys.argv 处理语义。
    传 ``argv`` 时按 pdfx 的既有约定临时替换 ``sys.argv``。
    """
    import sys

    module_main = require_document("pdfx.cli").main
    if argv is None:
        return module_main()
    old = sys.argv
    try:
        sys.argv = ["omnicrawler pdf", *argv]
        return module_main()
    finally:
        sys.argv = old


def field_extractor_main(argv: list[str] | None = None) -> Any:
    """转发到字段抽取命令的 main。"""
    return require_document("apps.field_extractor").main(argv)


def pdf_processor_main(argv: list[str] | None = None) -> Any:
    """转发到 PDF 处理命令的 main。"""
    return require_document("apps.pdf_processor").main(argv)


def convertx_module() -> Any:
    """取 convertx 模块本身（挂点需要 OutputRule / convert / presets）。"""
    return require_document("convertx")


def convert_with_preset(*args: Any, **kwargs: Any) -> Any:
    """按预设转换格式。"""
    return require_document("convertx").convert(*args, **kwargs)


def convertx_preset(name: str, path: Any = None) -> Any:
    """取一个转换预设（参数与 ``convertx.presets.get_preset`` 一致）。"""
    return require_document("convertx.presets").get_preset(name, path)


def compare_runs(*args: Any, **kwargs: Any) -> Any:
    """比较两次运行结果。"""
    return require_document("review.run_compare").compare_runs(*args, **kwargs)
