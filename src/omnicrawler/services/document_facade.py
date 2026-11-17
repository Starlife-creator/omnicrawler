"""PDF 工作台的宿主侧入口（2026-11-17 迁移后）。

迁移前后对比
------------
**迁移前**：`pdfx` / `convertx` / `apps` / `document_ir` 在宿主内，
本 facade 只是"统一 import 点"，用于收口 8 处散落挂点。

**迁移后**：这些包已整体迁到插件 `pdf-workbench`
（`OmniCrawler-market/plugins/pdf-workbench`），宿主里**不再有它们的代码**。
本 facade 的职责变成：

1. **报告可用性**：`document_capability()` —— 插件装没装；
2. **调用插件**：`run_operation()` —— 把操作转给插件；
3. **缺失时给可操作提示**：告诉用户去插件市场装，而不是抛 ImportError 栈。

为什么是 in_process 插件而不是子进程
------------------------------------
PDF 工作台要自由读写用户指定的项目目录（读 PDF、写五表、建 SQLite）。
契约 2 的 `files.read`（静态白名单）/ `artifact.stream`（无路径）/
`temp.open`（会话临时）**没有一条能覆盖**。实测契约 2 审计报 36 条
`gate1_uncontrolled_file_io`。契约 1（in_process）是这类需求的既定通道：
`plugin_audit.gate_host_boundaries` 对 `execution_mode: in_process` 直接豁免。

安全边界（迁移后仍生效）
------------------------
* 插件加载前仍要**签名验签** + 静态审计；
* 组件 OCR 仍经 `ComponentOCRBackend`（验签/注册表锁在宿主，不在插件）；
* LLM 出网仍受隐私闸门约束。
"""
from __future__ import annotations

from typing import Any

#: 迁移到插件后的插件 id。
_PLUGIN_ID = "pdf-workbench"

#: 迁移后宿主侧**不再保留**的包。
#:
#: 守卫用它检查"迁移是否做干净"：这些目录不该再出现在 `src/omnicrawler/` 下。
_MIGRATED_PACKAGES: tuple[str, ...] = ("pdfx", "convertx", "apps", "document_ir")

#: 插件提供的一次性操作（不属于管线阶段，由 `run_operation` 直调）。
OPERATIONS: tuple[str, ...] = (
    "pdf.selfcheck",
    "pdf.validate",
    "pdf.status",
    "pdf.apply_review",
    "processor.process",
    "exporter.export",
)


def _plugin_dir() -> Any:
    """返回插件安装目录（`<root>/plugins_installed/<id>`），找不到返回 None。

    宿主没有"已安装插件 id 列表"的现成 API：插件是按目录加载的
    （`plugin_loader` 递归扫 `plugins_installed/`）。这里沿用同一约定，
    直接查目录——比维护一份 id 清单更不容易与真实安装状态脱节。
    """
    from pathlib import Path

    candidates = []
    try:
        from ..core.config import load_config

        # 宿主没有无参的 AppConfig 工厂：配置从约定路径加载。
        config = load_config(Path.cwd() / "configs" / "omnicrawler.yaml")
        candidates.append(Path(config.root) / "plugins_installed" / _PLUGIN_ID)
        plugins_section = config.section("plugins")
        if isinstance(plugins_section, dict):
            for extra in plugins_section.get("paths", []) or []:
                candidates.append(Path(str(extra)) / _PLUGIN_ID)
    except Exception:  # noqa: BLE001 - 配置不可用时退回下面几处
        pass

    # 便携布局：插件可能就装在应用目录旁的 plugins_installed/
    try:
        from ..core.runtime_paths import portable_data_root

        candidates.append(portable_data_root() / "plugins_installed" / _PLUGIN_ID)
    except Exception:  # noqa: BLE001
        pass

    for candidate in candidates:
        try:
            if (candidate / "plugin.py").is_file():
                return candidate
        except OSError:
            continue
    return None


def _plugin_installed() -> bool:
    """插件 `pdf-workbench` 是否已安装（以"入口文件在不在"为准）。"""
    return _plugin_dir() is not None


def document_capability() -> dict[str, Any]:
    """报告 PDF 工作台是否可用，以及**为什么不可用**。

    返回::

        {"available": bool, "reason": str, "missing": [...], "plugin_installed": bool}

    **迁移后判据变化**：从"宿主里有没有模块"改为"**插件装没装**"。
    这反而更可靠——没有加载代价，也不存在"检测本身把模块拉起来"的问题。

    迁移前踩过的两个坑（留档，防止将来重蹈）：
    1. 用 `import_module` 探测会把整个 `pdfx`（含 OCR/parser/LLM）拉进内存，
       "可选组件"就成假的了；
    2. 改用 `find_spec` 仍会导入**父包**；只看文件存在又会把
       "文件在、但导入失败"误报成可用。
    """
    if _plugin_installed():
        return {"available": True, "reason": "", "missing": [], "plugin_installed": True}
    return {
        "available": False,
        "reason": (
            "PDF 工作台插件未安装。该功能已改为可选插件——"
            "请在「插件市场」中安装 pdf-workbench，或使用文字版流程"
            "（爬虫与 AI 主流程不受影响）。"
        ),
        "missing": [_PLUGIN_ID],
        "plugin_installed": False,
    }


def require_document(operation: str = "") -> Any:
    """确认文档能力可用；不可用时抛**可操作**的错误。

    迁移后不再"返回模块"（宿主里没有那些模块了），而是返回插件句柄。
    :param operation: 可选，写进错误信息便于定位是哪条路径缺能力。
    """
    if not _plugin_installed():
        suffix = f"（触发操作：{operation}）" if operation else ""
        raise RuntimeError(
            "PDF 工作台插件未安装，无法执行该操作"
            + suffix
            + "。\n请在「插件市场」中安装 pdf-workbench；"
            "爬虫与 AI 主流程不受影响。"
        )
    return _plugin_handle()


def _plugin_handle() -> Any:
    """取已安装插件的模块句柄（in_process 模式：与宿主同进程）。

    模块名带插件 id 前缀，避免与 `plugin_loader` 加载其它插件时的
    同名 `plugin` 模块冲突（多个插件都叫 `plugin.py`）。
    """
    import importlib
    import importlib.util
    import sys
    import uuid

    directory = _plugin_dir()
    if directory is None:
        raise RuntimeError(
            f"PDF 工作台插件未安装：找不到 {_PLUGIN_ID} 的 plugin.py。"
            "请在「插件市场」中安装后重试。"
        )

    module_name = f"_pdf_workbench_{uuid.uuid5(uuid.NAMESPACE_URL, str(directory)).hex[:8]}"
    if module_name in sys.modules:
        return sys.modules[module_name]

    entry = directory / "plugin.py"
    spec = importlib.util.spec_from_file_location(module_name, entry)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载 PDF 工作台插件入口：{entry}")
    module = importlib.util.module_from_spec(spec)
    # 插件包（omnicrawler_pdf）在插件目录下，须先入 path 才能 import。
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


def run_operation(operation: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """把一次操作转给插件执行。

    这是宿主侧**唯一**调用 PDF 工作台的入口——挂点不应直接摸插件内部。
    :param operation: 见 `OPERATIONS`。
    :param payload: 操作参数（`config_path` / `config` / `review` 等）。

    插件未安装时抛可操作错误；插件内部失败时返回 `{"ok": False, "error": ...}`
    （插件侧已把异常转成结构化错误，不丢栈给宿主）。
    """
    module = require_document(operation)
    runner = getattr(module, "run_operation", None)
    if runner is None:
        raise RuntimeError(
            f"PDF 工作台插件版本过旧：缺少 run_operation 入口。请升级 {_PLUGIN_ID}。"
        )
    return runner(operation, dict(payload or {}))


def migrated_packages_absent() -> dict[str, bool]:
    """迁移是否做干净：报告宿主侧是否仍存在已迁走的包。

    供守卫与诊断使用：`True` 表示该包**已不在**宿主里（正确状态）。
    """
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent  # omnicrawler/
    return {
        name: not (root / name / "__init__.py").is_file()
        for name in _MIGRATED_PACKAGES
    }

# ── 兼容入口：既有挂点仍用这些名字 ────────────────────────────────────────
#
# facade 重写时删掉了这组函数，导致 5 个挂点（workbench / pdf_integration /
# preflight / cli / handlers）全部报 `has no attribute`。
# 现在按**插件调用**语义补回：名字不变，内部转成插件操作。
# 这样挂点代码不用再改一遍（它们已经正确指向 facade）。


def load_pdf_config(path: Any) -> Any:
    """加载 PDF 项目配置（经插件）。

    返回的是插件内的 `ProjectConfig` 对象——与宿主同进程（in_process），
    所以可直接传递。这也是契约 1 的一个实际便利。
    """
    module = require_document("pdf.validate")
    return module.run_operation("pdf.load_config", {"config_path": str(path)})


def create_project_config(*args: Any, **kwargs: Any) -> Any:
    """创建 PDF 项目配置（经插件）。"""
    module = require_document("pdf.create_project")
    return module.run_operation(
        "pdf.create_project", {"args": list(args), "kwargs": dict(kwargs)}
    )


def run_pdf_extraction(*args: Any, **kwargs: Any) -> Any:
    """运行 PDF 抽取（经插件）。"""
    module = require_document("pdf.extract")
    return module.run_operation(
        "pdf.extract", {"args": list(args), "kwargs": dict(kwargs)}
    )


def run_pdf_processing(*args: Any, **kwargs: Any) -> Any:
    """运行 PDF 处理（经插件）。"""
    module = require_document("processor.process")
    return module.run_operation(
        "processor.process", {"args": list(args), "kwargs": dict(kwargs)}
    )


def pdf_database_status(database: Any) -> Any:
    """查询 PDF 数据库状态（经插件）。"""
    module = require_document("pdf.status")
    return module.run_operation("pdf.database_status", {"database": database})


def apply_pdf_review(*args: Any, **kwargs: Any) -> Any:
    """应用人工复核（经插件）。"""
    module = require_document("pdf.apply_review")
    return module.run_operation(
        "pdf.apply_review", {"args": list(args), "kwargs": dict(kwargs)}
    )


def pdf_main(argv: list[str] | None = None) -> Any:
    """转发到 PDF 子命令（经插件）。"""
    module = require_document("pdf.cli")
    return module.run_operation("pdf.cli", {"argv": list(argv or [])})


def pdf_processor_main(argv: list[str] | None = None) -> Any:
    """转发到 PDF 处理命令（经插件）。"""
    module = require_document("pdf.processor")
    return module.run_operation("pdf.processor", {"argv": list(argv or [])})


def field_extractor_main(argv: list[str] | None = None) -> Any:
    """转发到字段抽取命令（经插件）。"""
    module = require_document("pdf.field_extractor")
    return module.run_operation("pdf.field_extractor", {"argv": list(argv or [])})


def convertx_module() -> Any:
    """取格式转换模块（经插件）。"""
    return require_document("convertx")


def convert_with_preset(*args: Any, **kwargs: Any) -> Any:
    """按预设转换（经插件）。"""
    module = require_document("convertx")
    return module.run_operation("convertx.convert", {"args": list(args), "kwargs": dict(kwargs)})


def convertx_preset(name: str, path: Any = None) -> Any:
    """取转换预设（经插件）。"""
    module = require_document("convertx")
    return module.run_operation("convertx.preset", {"name": name, "path": path})


def compare_runs(*args: Any, **kwargs: Any) -> Any:
    """比较两次运行（经插件）。"""
    module = require_document("review.compare")
    return module.run_operation("review.compare", {"args": list(args), "kwargs": dict(kwargs)})


def ocr_backend_class() -> Any:
    """取组件式 OCR 后端类（经插件）。

    供 `plugins.plugin_broker_host` 的 `component.ocr.recognize` 使用。
    """
    module = require_document("pdf.ocr_backend")
    return module.component_ocr_backend_class()


def open_pdf_database(path: Any) -> Any:
    """打开 PDF 项目数据库（经插件）。"""
    module = require_document("pdf.database")
    return module.open_pdf_database(str(path))


def pdf_views() -> Any:
    """取 PDF 工作台的 GUI 视图模块（来自插件）。

    迁移后（2026-11-17）：`gui/views/pdf_*.py` 与 `convert_tool.py` 已迁入插件。
    宿主 `central_area` 经本函数按需加载：

    * 插件已装 ⇒ 返回带 `PdfWorkbenchView` / `ConvertView` 的模块；
    * 未装 ⇒ 调用方（`document_capability`）已先判过，这里不会走到。

    **返回模块而不是类**：视图之间的内部依赖（result/scan/worker）都在插件内，
    宿主不需要知道它们，只取两个入口类即可。
    """
    module = require_document("pdf.views")
    return module.pdf_views()


def table_io() -> Any:
    """取表格数据读写注册表（`READERS` / `WRITERS` / `sniff_format`）。

    `omnicrawler transform`（支柱①数据加工）需要它。实现随 `convertx` 迁入插件，
    宿主经本入口按需取用。
    """
    module = require_document("transform.table_io")
    return module.table_io()


def pdf_region_selector() -> Any:
    """取 PDF 区域选择对话框类（来自插件）。"""
    module = require_document("pdf.region_selector")
    return module.pdf_region_selector()


def parse_document(*args: Any, **kwargs: Any) -> Any:
    """解析文档为 IR（来自插件的 document_ir）。"""
    module = require_document("pdf.parse_document")
    return module.parse_document(*args, **kwargs)


def document_block_class() -> Any:
    """取 `DocumentBlock` 类（来自插件的 document_ir）。"""
    module = require_document("pdf.document_block")
    return module.document_block_class()
