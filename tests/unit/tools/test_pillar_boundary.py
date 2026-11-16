"""第 1 批精简：解开反向依赖与错位依赖（不含目录搬家）。

**背景**

用户的方向调整：只保留「爬虫+AI」与「插件市场」两条支柱，其余尽量精简；
但**能组件化的组件化、能保留的保留，不删心血**。

可达性分析（AST，533 模块）查出 20 个"支柱内引用文档子系统"的挂点。
本文件只钉住其中**不该存在**的几类——真正的挂点（PDF 工作台界面、
PDF 命令行转发）属正常，将来由 facade 收口。

**本文件钉住三类问题**

1. **反向依赖**：支柱②的组件管理器（`services.component_manager`）
   反过来 import 文档子系统的 `pdfx.ocr_engines`。
   这是我引入引擎注册表时留下的——当时为避开 `config ↔ ocr` 环，
   把注册表放进 `pdfx` 当叶子模块；但它**不属于文档子系统的语义**
   （它描述的是"宿主支持哪些 OCR 引擎"）。

2. **错位依赖：Markdown 导出**。
   `gui.views.result_table` / `gui.main` / `gui.views.professional_review`
   为了导出 Markdown 反向依赖 `export.markdown_exporter`。
   但"把采集结果导出成 Markdown"是**支柱①的交付能力**，
   不是文档工作台的能力。导出归支柱①（用户已拍板）。

3. **错位依赖：文本抽取**。
   `quality.gene_augment` 借用 `doc_extractors` 的 regex/jsonpath/text 抽取。
   基因增强属于支柱①的数据加工，不该依赖文档工作台。

**判据**

* 上述三个方向**不得**再出现（AST 扫描，含函数内惰性导入——
  惰性导入同样会把模块拉进依赖闭包）；
* 能力本身**必须保留**（导出仍能出 Markdown、增强仍能抽 regex）——
  解耦不是砍功能。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src" / "omnicrawler"

#: 文档子系统的包名（将来要被组件化的那部分）。
_DOC_PACKAGES = frozenset({
    "pdfx", "convertx", "document_ir", "doc_extractors", "export", "review", "apps",
})


def _imports_of(path: Path) -> dict[int, list[str]]:
    """返回 {行号: [包内模块名]}（含函数内导入，**含相对导入**）。

    **相对导入必须解析**：本仓库大量使用 ``from ..pdfx.ocr_engines import ...``，
    早先的实现对 ``node.level > 0`` 直接 ``continue``，于是这类依赖
    **完全检测不到**——反向断言时把反向依赖装回去，守卫依然是绿的
    （实测踩到，已在这里修正）。

    解析方式：按文件所在包把 ``..x.y`` 还原成 ``omnicrawler`` 下的绝对路径。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    rel = path.relative_to(_SRC)
    parts = list(rel.with_suffix("").parts)
    if parts[-1] == "__init__":
        package = parts[:-1]
    else:
        package = parts[:-1]

    result: dict[int, list[str]] = {}
    for node in ast.walk(tree):
        targets: list[str] = []
        if isinstance(node, ast.Import):
            targets = [
                alias.name[len("omnicrawler."):]
                for alias in node.names
                if alias.name.startswith("omnicrawler")
            ]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            level = node.level or 0
            if level > 0:
                # from ..a.b import c  ⇒ level=2 表示上跳一级
                base = package[: len(package) - (level - 1)] if level > 1 else package
                joined = ".".join([*base, module]) if module else ".".join(base)
                targets = [joined]
            elif module.startswith("omnicrawler"):
                targets = [module[len("omnicrawler."):]]
        if targets:
            result[node.lineno] = targets
    return result


def _doc_imports_in(relative: str) -> list[str]:
    """列出某文件里引用文档子系统的位置。"""
    path = _SRC / relative
    if not path.is_file():
        pytest.skip(f"文件不存在：{relative}")
    offenders: list[str] = []
    for lineno, targets in _imports_of(path).items():
        for target in targets:
            if target.split(".")[0] in _DOC_PACKAGES:
                offenders.append(f"{relative}:{lineno} -> {target}")
    return offenders


def test_component_manager_does_not_depend_on_pdfx() -> None:
    """组件管理器（支柱②）不得依赖文档子系统。"""
    offenders = _doc_imports_in("services/component_manager.py")
    assert offenders == [], (
        "组件管理器仍反向依赖文档子系统：\n  " + "\n  ".join(offenders) +
        "\n组件管理器属于支柱②（插件市场/组件），OCR 引擎注册表不属于文档子系统。"
        "应把它上提到宿主侧（services/ 或 core/）。"
    )


def test_ocr_engine_registry_lives_in_host() -> None:
    """OCR 引擎注册表必须位于宿主侧，不在 `pdfx` 里。"""
    assert (_SRC / "services" / "ocr_engines.py").is_file() or (
        _SRC / "core" / "ocr_engines.py"
    ).is_file(), (
        "OCR 引擎注册表仍在 pdfx 里。它描述的是「宿主支持哪些引擎」，"
        "属于支柱②的组件能力，不是文档子系统的一部分。"
    )


def test_markdown_export_entry_exists_and_callers_use_it() -> None:
    """Markdown 导出必须有**宿主侧稳定入口**，且支柱侧调用方经由它。

    第 1 批只做解耦、不搬目录（用户要求保留既有资产），所以实现仍在
    `export/markdown_exporter.py`；**但支柱侧的调用方不得再直接摸它**——
    将来搬成组件时只需改入口一处，而不是满地找调用点。
    """
    from omnicrawler.services.markdown_export import export_results_markdown

    assert callable(export_results_markdown)

    # 三个 GUI 挂点都不得再直接导入 export.markdown_exporter
    for relative in (
        "gui/views/result_table.py",
        "gui/main.py",
        "gui/views/professional_review.py",
    ):
        path = _SRC / relative
        if not path.is_file():
            pytest.skip(f"文件不存在：{relative}")
        text = path.read_text(encoding="utf-8")
        assert "export.markdown_exporter" not in text, (
            f"{relative} 仍直接依赖 export.markdown_exporter；"
            "应改用 services.markdown_export.export_results_markdown"
        )


def test_result_export_does_not_import_document_subsystem() -> None:
    """结果表导出不得经由文档子系统。"""
    offenders = _doc_imports_in("gui/views/result_table.py")
    assert offenders == [], (
        "结果表导出仍依赖文档子系统：\n  " + "\n  ".join(offenders)
    )


def test_gene_augment_does_not_import_document_subsystem() -> None:
    """基因增强（支柱①数据加工）不得依赖文档抽取器。"""
    offenders = _doc_imports_in("quality/gene_augment.py")
    assert offenders == [], (
        "gene_augment 仍依赖 doc_extractors：\n  " + "\n  ".join(offenders) +
        "\nregex / jsonpath / text 抽取是通用能力，应放在宿主侧"
    )


def test_capabilities_preserved_after_decoupling() -> None:
    """解耦不是砍功能：两边能力都必须仍然可用（**按真实行为**验证）。"""
    import tempfile
    from pathlib import Path as _Path

    # 1) Markdown 导出仍能真的产出文件
    from omnicrawler.services.markdown_export import export_results_markdown

    workdir = _Path(tempfile.mkdtemp(prefix="pillar-md-"))
    csv_path = workdir / "results.csv"
    csv_path.write_text("a,b\n1,2\n", encoding="utf-8")
    output = export_results_markdown(csv_path)
    assert output.is_file(), "Markdown 导出没有产出文件"
    assert "| a | b |" in output.read_text(encoding="utf-8"), "导出内容格式变了"

    # 2) 文本抽取仍能按规则取值
    from omnicrawler.services.text_extraction import extract_text_field

    assert extract_text_field("abc123", source_type="regex", selector=r"(\d+)") == "123"
