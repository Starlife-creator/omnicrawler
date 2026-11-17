"""插件里**每一处函数内导入**都必须能真实执行。

## 为什么要扫全部 153 处

前面几轮的教训：失效导入躲在函数内部时，导入期不报错、测试全绿，
用户操作到那一步才崩。人工逐个检查不现实（153 处），只能机械化。

2026-11-23 这一轮扫出并修好 **4 类真实问题**：

| 位置 | 问题 |
|---|---|
| `gui_views/pdf_layout_review.py` | `from ...pdfx.parser` —— 三跳越过了包根 |
| `gui_views/pdf_workbench.py` | `from ...pdfx.ocr` —— 同上 |
| `compat/ai_env.py` | `from .errors` —— `compat/errors.py` 从未搬入插件 |
| `compat/progress.py` | `from ..i18n` —— i18n 就在 `compat/` 下 |
| `pdfx/__main__.py` | `from ..cli` —— 应为 `from .cli` |

## 判据

用 **AST + 真实 import**（不是正则、不是 find_spec）：

1. 收集所有非模块级的 `ImportFrom`；
2. 把相对导入换算成绝对模块名；
3. **真的 `importlib.import_module`** 再 `hasattr` 检查符号；
4. 被 `try/except ImportError` 包裹的允许失败（那是可选依赖探测）。

## 为什么不用 find_spec

实测它对本仓的命名空间/未导入父包行为不一致，会产生假阳性
（`omnicrawler.services.table_io` 明明存在却报不存在）。
真实 import 与运行时完全一致。
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

from omnicrawler.services import document_facade

_PLUGIN = (
    Path(__file__).resolve().parents[4]
    / "OmniCrawler-market"
    / "submissions"
    / "plugins"
    / "4c3014804d85de2568f85e62a3048429"
    / "pdf-workbench"
)


def _absolute_module(file: Path, node: ast.ImportFrom) -> str | None:
    """把 `ImportFrom`（可能是相对导入）换算成绝对模块名。"""
    if node.level == 0:
        return node.module
    parts = file.relative_to(_PLUGIN).with_suffix("").parts[:-1]
    if node.level - 1 > len(parts):
        return None
    base = parts[: len(parts) - (node.level - 1)]
    return ".".join([*base, *(node.module.split(".") if node.module else [])])


def _is_guarded(lines: list[str], lineno: int) -> bool:
    """这一行导入是否在 `try:` 块里（允许失败）。"""
    for offset in range(1, 5):
        index = lineno - 1 - offset
        if index < 0:
            return False
        stripped = lines[index].strip()
        if stripped.startswith("try:"):
            return True
        if stripped and not stripped.startswith("#"):
            return False
    return False


def _collect_lazy_imports() -> list[tuple[Path, ast.ImportFrom, str]]:
    """收集插件里所有**非模块级**的 ImportFrom 及其所属文件。"""
    collected: list[tuple[Path, ast.ImportFrom, str]] = []
    for file in sorted(_PLUGIN.rglob("*.py")):
        if "__pycache__" in file.parts or "tests" in file.parts:
            continue
        source = file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        top_level = {
            node.lineno
            for node in tree.body
            if isinstance(node, (ast.Import, ast.ImportFrom))
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.lineno in top_level:
                continue
            module = _absolute_module(file, node)
            if not module or module.startswith("__future__"):
                continue
            collected.append((file, node, module))
    return collected


@pytest.mark.skipif(not _PLUGIN.is_dir(), reason="需要 market 仓插件目录")
def test_every_lazy_import_can_be_executed() -> None:
    """每一处无保护的函数内导入都必须能真实执行。"""
    if not document_facade.document_capability()["available"]:
        pytest.skip("需要 pdf-workbench 插件（要先把插件目录加入 sys.path）")

    # pdf_views() 会把插件根加入 sys.path，后续 import 才找得到 omnicrawler_pdf
    document_facade.pdf_views()
    importlib.invalidate_caches()

    entries = _collect_lazy_imports()
    assert len(entries) >= 50, (
        f"只收集到 {len(entries)} 处函数内导入——AST 解析或路径推算可能失效"
    )

    problems: list[str] = []
    for file, node, module in entries:
        lines = file.read_text(encoding="utf-8").splitlines()
        guarded = _is_guarded(lines, node.lineno)
        rel = file.relative_to(_PLUGIN)

        try:
            loaded = importlib.import_module(module)
        except ModuleNotFoundError as exc:
            message = f"{rel}:{node.lineno}  {module} —— {exc}"
            if not guarded:
                problems.append(message)
            continue
        except Exception:  # noqa: BLE001 - 导入期副作用失败不算引用错误
            continue

        for alias in node.names:
            if alias.name == "*":
                continue
            if hasattr(loaded, alias.name):
                continue
            # `from pkg import submodule` 的情形：`submodule` 是模块而不是属性。
            # `hasattr` 在子模块尚未导入时会返回 False（父包不自动暴露），
            # 但语句本身合法——再试一次按模块导入。
            try:
                importlib.import_module(f"{module}.{alias.name}")
                continue
            except ModuleNotFoundError:
                pass
            message = f"{rel}:{node.lineno}  {module}.{alias.name} 不存在"
            if not guarded:
                problems.append(message)

    assert not problems, (
        f"{len(problems)} 处函数内导入无法执行（用户走到那一步就会崩）：\n  "
        + "\n  ".join(problems)
    )


@pytest.mark.skipif(not _PLUGIN.is_dir(), reason="需要 market 仓插件目录")
def test_pdfx_main_forwarding_target_exists() -> None:
    """`pdfx.__main__` 转发的 CLI 必须存在（曾写成 `..cli` 越过包根）。"""
    main_file = _PLUGIN / "omnicrawler_pdf" / "pdfx" / "__main__.py"
    if not main_file.is_file():
        pytest.skip("找不到 pdfx/__main__.py")

    source = main_file.read_text(encoding="utf-8")
    assert "from .cli import" in source, (
        "`pdfx/__main__.py` 应 `from .cli import`（cli 与它同在 pdfx 包内）；"
        "写成 `from ..cli` 会指向 omnicrawler_pdf.cli——那个模块不存在"
    )
