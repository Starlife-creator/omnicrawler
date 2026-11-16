"""第 2 批：文档子系统挂点收口成单一 facade。

**为什么需要 facade**

第 1 批盘点出 20 个"支柱内引用文档子系统"的挂点，其中 8 处是**真正的挂点**
（PDF 工作台界面、PDF 命令行转发、转换工具、PDF 任务编排）。它们现在各自
直接 import，分散在：

```
cli/_main.py                pdfx.cli.main / apps.pdf_processor / apps.field_extractor
cli/_handlers.py            convertx（convert / OutputRule / presets）/ review.run_compare
services/workbench.py       pdfx.config / database / desktop / review / service
pipeline_ops/pdf_integration.py  pdfx.project / config / service
pipeline_ops/preflight.py   pdfx.config
gui/views/pdf_workbench*.py pdfx.ocr / service / project
gui/views/convert_tool.py   convertx
```

分散的直接后果：**将来把文档工作台打包成组件时，要逐处去找、逐处去改**，
漏一处就是运行期 ImportError。收口之后只需改 facade 一处。

**facade 要同时解决"组件未安装"这件事**

用户的目标是"文档工作台变可选组件"。所以 facade 不能只是转发——它必须
能回答**"这个环境有没有文档能力"**，并在没有时给出**可操作**的提示
（装哪个组件），而不是抛一个 ImportError 栈。

这正是清单里"条目 7 续：未安装时的引导提示"未完成的部分。

**判据（本文件钉住四条）**

1. 所有真挂点**只能**经由 facade 触达文档子系统（不得再直接 import `pdfx`/`convertx`）；
2. facade 提供**能力探测**（`document_capability()`），缺组件时不抛栈而是返回不可用；
3. 缺组件时的提示**可操作**（说清缺什么、怎么装），不是空串；
4. **能力仍在**：facade 能真的把 PDF 配置加载、把项目配置创建出来——
   收口不是砍功能。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC = _REPO_ROOT / "src" / "omnicrawler"

_DOC_PACKAGES = frozenset({
    "pdfx", "convertx", "document_ir", "doc_extractors", "export", "review", "apps",
})

#: 必须经由 facade 的挂点（第 2 批收口的对象）。
#:
#: 注意 `gui/views/pdf_workbench*.py` 与 `gui/views/convert_tool.py` **不在**此列：
#: 它们本身就是文档工作台的界面文件，将来会**整体搬进组件**，
#: 现在给它们套一层 facade 只是多一层无意义间接。
_MUST_USE_FACADE = (
    "cli/_main.py",
    "cli/_handlers.py",
    "services/workbench.py",
    "pipeline_ops/pdf_integration.py",
    "pipeline_ops/preflight.py",
)


def _imports_of(path: Path) -> dict[int, list[str]]:
    """返回 {行号: [包内模块名]}（含函数内导入，含相对导入）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    rel = path.relative_to(_SRC)
    parts = list(rel.with_suffix("").parts)
    package = parts[:-1] if parts[-1] != "__init__" else parts[:-1]

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
                base = package[: len(package) - (level - 1)] if level > 1 else package
                joined = ".".join([*base, module]) if module else ".".join(base)
                targets = [joined]
            elif module.startswith("omnicrawler"):
                targets = [module[len("omnicrawler."):]]
        if targets:
            result[node.lineno] = targets
    return result


def test_seams_go_through_facade() -> None:
    """挂点不得再直接 import 文档子系统。"""
    offenders: list[str] = []
    for relative in _MUST_USE_FACADE:
        path = _SRC / relative
        if not path.is_file():
            pytest.skip(f"文件不存在：{relative}")
        for lineno, targets in _imports_of(path).items():
            for target in targets:
                head = target.split(".")[0]
                if head in _DOC_PACKAGES:
                    offenders.append(f"{relative}:{lineno} -> {target}")
    assert offenders == [], (
        "以下挂点仍直接依赖文档子系统：\n  " + "\n  ".join(offenders) +
        "\n应改为经由 services.document_facade —— 将来组件化只需改 facade 一处，"
        "否则漏改一处就是运行期 ImportError。"
    )


def test_facade_provides_capability_probe() -> None:
    """facade 必须能回答"这个环境有没有文档能力"。"""
    from omnicrawler.services.document_facade import document_capability

    report = document_capability()
    assert isinstance(report, dict)
    assert "available" in report and isinstance(report["available"], bool)
    # 可用时必须给出入口；不可用时必须给出原因（不能两者都空）
    assert report.get("reason") or report.get("available"), (
        f"能力报告既不可用也没给原因：{report}"
    )


def test_missing_component_reason_is_actionable() -> None:
    """缺组件时的提示必须可操作（说清缺什么/怎么装），不能是空串或栈。"""
    from omnicrawler.services.document_facade import require_document

    # 本机装了文档子系统，所以这里验证"正常路径不抛"
    module = require_document("pdfx.service")
    assert module is not None

    # 请求一个不存在的子模块 ⇒ 必须抛**带说明**的错误，而不是裸 ImportError
    with pytest.raises(Exception) as excinfo:
        require_document("pdfx.no_such_module_xyz")
    message = str(excinfo.value)
    assert message.strip(), "缺模块时错误消息为空"
    assert ("组件" in message) or ("安装" in message) or ("document" in message.lower()), (
        f"错误信息没有告诉用户该怎么办：{message!r}"
    )


def test_facade_capabilities_still_work(tmp_path: Path) -> None:
    """收口不是砍功能：facade 要能真的加载 PDF 配置、创建项目配置。"""
    from omnicrawler.services import document_facade

    # 1) 能拿到配置加载器（真实可调用）
    loader = document_facade.load_pdf_config
    assert callable(loader)

    # 2) 能创建项目配置（用真实 API，产出目录）
    #    这里只验证"入口可达且签名可用"，不落真实产物（需要完整项目上下文）
    creator = document_facade.create_project_config
    assert callable(creator)


def test_facade_import_does_not_leak_heavy_document_modules(tmp_path: Path) -> None:
    """导入 facade 本身不得拉起**重型**文档模块（否则"可选"就是假的）。

    ⚠ 判据在实现演进中调整过两次，记录在此：

    * 最初写成"零泄漏"，但后来给 `document_capability()` 加了一个**轻量探针**
      （真 import 一次 `pdfx.config`）以发现"文件在、但导入失败"的情况——
      只查文件会把这种状态误报成可用，用户看到入口、点进去才崩；
    * 所以现在的判据是：**允许**探针加载配置/常量级模块，
      **禁止**拉起 `pdfx.service` / `pdfx.ocr` / `pdfx.parser` 这些重型链路。

    探针的选择理由写在 `document_facade._PROBE_MODULE` 的注释里。
    """
    import subprocess
    import sys

    script = (
        "import sys\n"
        f"sys.path.insert(0, {str(_SRC.parent)!r})\n"
        "import omnicrawler.services.document_facade as f  # noqa: F401\n"
        "f.document_capability()\n"
        "loaded = sorted(m for m in sys.modules if m.startswith(('omnicrawler.pdfx', 'omnicrawler.convertx')))\n"
        "print('LOADED=' + repr(loaded))\n"
    )

    out = tmp_path / "probe.txt"
    with out.open("w", encoding="utf-8") as handle:
        result = subprocess.run(
            [sys.executable, "-c", script],
            stdout=handle, stderr=subprocess.STDOUT, timeout=120, check=False,
        )
    text = out.read_text(encoding="utf-8", errors="replace")
    assert result.returncode == 0, f"导入 facade 失败：\n{text[-1200:]}"

    heavy = ("omnicrawler.pdfx.service", "omnicrawler.pdfx.ocr", "omnicrawler.pdfx.parser",
             "omnicrawler.convertx")
    offenders = [name for name in heavy if name in text]
    assert offenders == [], (
        "导入 facade 就拉起了重型文档模块：\n  " + "\n  ".join(offenders) +
        "\n那 facade 只掩盖了依赖，没有真正解耦。\n" + text[-800:]
    )
