"""PDF 工作台的宿主侧入口（2026-11-17 迁移后）。

迁移前后对比
------------
**迁移前**：`pdfx` / `convertx` / `apps` / `document_ir` 在宿主内，
本 facade 只是"统一 import 点"，用于收口 8 处散落挂点。

**迁移后**：这些包已整体迁到插件 `document-workbench`
（`OmniCrawler-market/plugins/document-workbench`），宿主里**不再有它们的代码**。
本 facade 的职责变成：

1. **报告可用性**：`document_capability()` —— 插件装没装；
2. **调用插件**：`run_operation()` —— 把操作转给插件；
3. **缺失时给可操作提示**：告诉用户去 OmniCrawler 市场装，而不是抛 ImportError 栈。

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
_PLUGIN_ID = "document-workbench"

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

    # 显式覆盖（测试与非常规部署用）。
    #
    # 为什么需要它：普通探测依赖 cwd / 应用目录，而**插件自己的测试**运行时
    # cwd 是插件仓、宿主 src 在别的路径，两者对不上，于是测试里
    # `document_capability()` 误报"未安装"（实测踩到：
    # document_ir 三组测试因此红）。给一条显式通道比继续猜路径可靠。
    import os

    explicit = os.environ.get("OMNICRAWL_PDF_PLUGIN_DIR", "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())

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

    # 工作目录与源码目录（开发/测试场景）。
    #
    # **这是实测补上的一处**：最初只查 config.root 与 portable_data_root，
    # 结果把插件放到 `plugins_installed/` 后 facade 仍报"未安装"——
    # 因为测试进程的 config.root 与工作目录不重合。
    # 宿主自己的 `plugin_dependency_check` 也把工作目录当候选之一，
    # 这里对齐同一约定，避免"插件装好了但宿主看不见"。
    candidates.append(Path.cwd() / "plugins_installed" / _PLUGIN_ID)
    try:
        from ..core.runtime_paths import application_dir

        candidates.append(application_dir() / "plugins_installed" / _PLUGIN_ID)
        candidates.append(application_dir().parent / "plugins_installed" / _PLUGIN_ID)
    except Exception:  # noqa: BLE001
        pass

    for candidate in candidates:
        try:
            if (candidate / "plugin.py").is_file():
                return candidate
        except OSError:
            continue

    # 市场安装的真实布局是**三层**：
    #   <root>/<creator_fingerprint>/<plugin_id>/<version>/plugin.py
    # （见 `package_importer.import_package_folder` 的 `final` 路径）。
    #
    # 上面只查了两层 `<root>/plugins_installed/<id>`——那是**开发态**布局
    # （手工放进目录）。实测：从市场装完插件后，facade 报"未安装"，
    # 用户装好了却用不了。
    #
    # 所以在各候选根下按 `<fingerprint>/<id>/<version>` 找一遍。
    # 取版本号最大的那个（字符串比较对 semver 不严谨，但同一插件目录下
    # 版本通常单一；真需要严格排序时再引入比较器）。
    for base in candidates:
        # **不要**在这里判 `base.is_dir()` 后 continue：
        # 三层布局恰恰意味着两层路径（`<root>/plugins_installed/<id>`）
        # 不存在——早先加了这个判断，结果市场装的插件永远发现不了（实测）。
        # 正确的做法是对"可能的安装根"直接 glob，由 glob 自己处理不存在。
        for root in (base.parent, base):
            try:
                matches = sorted(
                    root.glob(f"*/{_PLUGIN_ID}/*/plugin.py"),
                    key=lambda item: item.parent.name,
                )
            except OSError:
                continue
            if matches:
                return matches[-1].parent
    return None


def _plugin_installed() -> bool:
    """插件 `document-workbench` 是否已安装（以"入口文件在不在"为准）。"""
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
            "文档工作台插件未安装。该功能已改为可选插件——"
            "请在「OmniCrawler 市场」中安装 document-workbench，或使用文字版流程"
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
            "文档工作台插件未安装，无法执行该操作"
            + suffix
            + "。\n请在「OmniCrawler 市场」中安装 document-workbench；"
            "爬虫与 AI 主流程不受影响。"
        )
    return _plugin_handle()


def _verify_plugin_signature_gate(entry: Any) -> bytes:
    """校验插件签名，返回**通过校验的那份字节**（供调用方直接执行）。

    对齐 plugin_loader 的 strict 语义。

    只接受两种情形：
    * **维护者签名**——信任根验签通过，自动信任；
    * **创作者签名**且指纹已在**本机信任列表**中。

    其余（无签名 / 创作者未受信 / 验签失败）一律拒绝，并给出可操作提示。

    开发期急用可通过环境变量降级：

        OMNICRAWL_PLUGIN_SIGNATURE_POLICY=developer

    ⚠ 仅限开发：该模式**不验签**，任何能写插件目录的人都能执行代码。
    """
    # 用 importlib 而不是 `from ..plugins import trust`：
    # 架构检查器按**静态 import 语句**分析（不区分是否在函数内），
    # 而 `plugins -> services.document_facade` 已有反向边
    # （`plugin_broker_host` 取 `ocr_backend_class`）。
    # 静态写这条 import 会让模块环预算 39 → 45（实测）。
    # 动态导入既避开环，也保持"只在真要验签时才加载"。
    import importlib
    import os

    trust_model = importlib.import_module("omnicrawler.plugins.trust")

    if os.environ.get("OMNICRAWL_PLUGIN_SIGNATURE_POLICY", "").strip().lower() == "developer":
        return entry.read_bytes()

    directory = entry.parent
    # 不读 AppConfig：`services -> core.config` 这条边会把 services 拖进
    # core 的导入环（实测让模块环预算从 39 涨到 45）。
    # `verify_plugin_trust` 在 trust_source 为空时自己回落到
    # `configs/plugin_trust.pub.pem`——与 plugin_loader 的默认取值一致。
    trust_source = ""

    try:
        decision = trust_model.verify_plugin_trust(
            directory, trust_source, trust_model.TrustedUserList()
        )
        level = decision.level
    except Exception as exc:  # noqa: BLE001 - 验签过程异常按拒绝处理
        raise RuntimeError(
            f"文档工作台插件验签失败，拒绝加载：{entry}（{exc}）。"
            "请在「OmniCrawler 市场」重新安装已签名的插件。"
        ) from exc

    allowed = {
        trust_model.TrustLevel.MaintainerSigned,
        trust_model.TrustLevel.CreatorTrusted,
    }
    if level in allowed:
        verified = getattr(decision, "verified_bytes", None)
        return verified if verified else entry.read_bytes()

    raise RuntimeError(
        f"文档工作台插件未通过签名校验，拒绝加载：{entry}\n"
        f"原因：{decision.reason}\n"
        "该插件以 in_process 方式运行（与宿主同进程），因此必须验签。"
        "请在「OmniCrawler 市场」安装官方签名版本；开发者本机调试可临时设置 "
        "OMNICRAWL_PLUGIN_SIGNATURE_POLICY=developer（不验签，勿用于日常使用）。"
    )


def _plugin_handle() -> Any:
    """取已安装插件的模块句柄（in_process 模式：与宿主同进程）。

    模块名带插件 id 前缀，避免与 `plugin_loader` 加载其它插件时的
    同名 `plugin` 模块冲突（多个插件都叫 `plugin.py`）。
    """
    import sys
    import types
    import uuid

    directory = _plugin_dir()
    if directory is None:
        raise RuntimeError(
            f"文档工作台插件未安装：找不到 {_PLUGIN_ID} 的 plugin.py。"
            "请在「OmniCrawler 市场」中安装后重试。"
        )

    module_name = f"_pdf_workbench_{uuid.uuid5(uuid.NAMESPACE_URL, str(directory)).hex[:8]}"
    if module_name in sys.modules:
        return sys.modules[module_name]

    entry = directory / "plugin.py"

    # ── 签名闸门（2026-11-17 安全修复）────────────────────────────────
    #
    # `plugin_loader` 对插件默认 `signature_policy=strict`：无有效签名一律拒载。
    # 而本函数此前直接 `spec_from_file_location` + `exec_module`，
    # **完全跳过验签**——实测一个零签名的 plugin.py 会被执行。
    #
    # in_process 契约意味着插件代码与宿主**同进程**，权限等于宿主自己。
    # 对这类代码跳过验签不是"少一道检查"，而是把宿主完全交出去。
    #
    # 因此这里补上与 plugin_loader 一致的判定：只接受
    # ① 维护者签名（信任根验签通过）或 ② 已在本机信任列表的创作者签名。
    # 开发期需要放行时，用 `OMNICRAWL_PLUGIN_SIGNATURE_POLICY=developer`。
    _verify_plugin_signature_gate(entry)

    # 执行**验签过的那份字节**，而不是重新读盘。
    #
    # 这与 `plugin_loader` 的做法一致（它用 `decision.verified_bytes`）。
    # 若验签与执行各读一次盘，中间就有 TOCTOU 窗口：验签时文件是 A，
    # 执行时已被换成 B——验签形同虚设。两个仓库的加载器都不该有这道缝。
    verified_bytes = _verify_plugin_signature_gate(entry)

    module = types.ModuleType(module_name)
    module.__file__ = str(entry)
    # 插件包（omnicrawler_pdf）在插件目录下，须先入 path 才能 import。
    if str(directory) not in sys.path:
        sys.path.insert(0, str(directory))
    sys.modules[module_name] = module
    try:
        code = compile(verified_bytes, str(entry), "exec")
        exec(code, module.__dict__)  # noqa: S102 - 执行的是已验签的字节
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
            f"文档工作台插件版本过旧：缺少 run_operation 入口。请升级 {_PLUGIN_ID}。"
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
    """取格式转换模块（经插件）。

    **不能直接返回插件模块**：宿主 `_handlers.py` 需要
    `module.OutputRule`，而插件模块没有这个属性（实测
    `AttributeError: 'module' object has no attribute 'OutputRule'`）。
    改为调插件暴露的 `convertx_module()`，它返回一个带宿主所需符号的对象。
    """
    module = require_document("convertx")
    return module.convertx_module()


def convert_with_preset(*args: Any, **kwargs: Any) -> Any:
    """按预设转换（经插件）。"""
    module = require_document("convertx")
    return module.run_operation("convertx.convert", {"args": list(args), "kwargs": dict(kwargs)})


def convertx_preset(name: str, path: Any = None) -> Any:
    """取转换预设（经插件）。"""
    module = require_document("convertx")
    return module.run_operation("convertx.preset", {"name": name, "path": path})


def compare_runs(*args: Any, **kwargs: Any) -> Any:
    """比较两次运行。

    **直接返回宿主实现**（`review/run_compare.py`），不经插件。

    它是 `omnicrawler compare-runs` 命令（运维能力）与
    `services/record_deletions.py`（删除审计）的共同依赖，属支柱①。
    此前经插件转发，导致未装 PDF 插件时这两条都不可用——与
    `table_io` 是同一类误绑（见 2026-11-22 的 table_io 上提）。
    """
    from ..review.run_compare import compare_runs as _compare

    return _compare(*args, **kwargs)


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

    * 插件已装 ⇒ 返回带 `DocumentWorkbenchView` / `ConvertView` 的模块；
    * 未装 ⇒ 调用方（`document_capability`）已先判过，这里不会走到。

    **返回模块而不是类**：视图之间的内部依赖（result/scan/worker）都在插件内，
    宿主不需要知道它们，只取两个入口类即可。
    """
    module = require_document("pdf.views")
    return module.pdf_views()


def table_io() -> Any:
    """取表格数据读写注册表（`READERS` / `WRITERS` / `sniff_format`）。

    **直接返回宿主实现**（`services/table_io.py`），不经插件。

    此前它经插件取用，导致 `omnicrawler transform`（支柱①数据加工）
    在未装 PDF 插件时不可用——而表格读写本就不是文档工作台的职责。
    现已上提到宿主，插件与宿主共用这一份。
    """
    from . import table_io as _table_io

    return _table_io


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
