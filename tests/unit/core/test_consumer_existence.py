"""S3.2.2 ③：消费方存在性测试——关键守卫/配置项必须有真实消费方，零消费即红。"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[3] / "src" / "omnicrawl"


def _source_text() -> str:
    texts: list[str] = []
    for path in SRC.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        try:
            texts.append(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return "\n".join(texts)


@pytest.fixture(scope="module")
def source() -> str:
    return _source_text()


def _docstring_nodes(tree: ast.AST) -> set[int]:
    """收集作为 docstring 的字符串常量节点 id（模块/函数/类首条语句）。"""
    doc_nodes: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            if body and isinstance(body[0], ast.Expr):
                value = body[0].value
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    doc_nodes.add(id(value))
    return doc_nodes


def _usage(source: str, token: str) -> int:
    """统计 token 在源码中的真实引用次数（AST，排除注释与 docstring）。

    原行扫描会把内联注释/docstring 续行/字符串里的同名符号误计为消费方，
    导致「零消费即红」守卫可被纯文档符号绕过（P1-4）。AST 统计：
    - ast.Name / ast.Attribute（标识符引用，含函数/属性/类名）
    - 非 docstring 的字符串常量（覆盖 value_pattern 等配置键字面量）
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    doc_nodes = _docstring_nodes(tree)
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == token:
            count += 1
        elif isinstance(node, ast.Attribute) and node.attr == token:
            count += 1
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            # 模块导入是真实引用：token 出现在任一导入名/模块路径的路径段中
            names = [alias.name for alias in node.names]
            if isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            if any(token in n.split(".") for n in names if n):
                count += 1
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value == token
            and id(node) not in doc_nodes
        ):
            count += 1
    return count


@pytest.mark.parametrize(
    "token",
    [
        "safe_regex_search",          # S2.5.14 核心防护——必须被消费
        "value_pattern",              # S3.2.1 ①：配置项消费方（pdfx config+validation）
        "history_max_entries",        # S3.2.1 ②：配置项消费方（settings+main 构造）
        "validate_ai_output",         # S3.2.1 ⑤：接入生产（ai_graph）
        "ai_audit_record",            # AI 审计（ai_task_designer）
        "seal_secret",                # S2.2.2 密封出口
        "pending_count",              # S2.5.37 增量统计
        "background_worker",          # S3.1.1 基类
        "NavIndex",                   # S3.1.2 导航常量
        "retry_after_cap_seconds",    # S2.5.9 配置项
        "partial_success",            # S2.4.1 状态
    ],
)
def test_guard_or_config_has_consumer(source: str, token: str) -> None:
    assert _usage(source, token) >= 1, f"{token} 无消费方（零消费孤儿）"


def test_deprecated_archives_still_importable() -> None:
    from omnicrawl.fetching.archives import ArchiveLimits, safe_extract_archive  # noqa: F401

    assert callable(safe_extract_archive)
    assert callable(ArchiveLimits)
    assert "已废弃" in _source_text()


def test_experimental_components_are_marked() -> None:
    text = _source_text()
    assert "实验性" in text  # AIGraphExtractor/ProxyRotator/apply_to_playwright_context 标注存在
    assert "已废弃" in text  # archives.py deprecated 标注存在
