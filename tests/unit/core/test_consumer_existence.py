"""S3.2.2 ③：消费方存在性测试——关键守卫/配置项必须有真实消费方，零消费即红。

性能约定（2026-09-21）：整棵 `src/omnicrawler` 源码树（417 文件 / 约 870 KB）在单个
测试 session 内只允许**读取一次、解析一次**，供全部 token 复用。

原实现把 `ast.parse(全仓源码)` 放在 `_usage()` 里，11 个 token 各自解析整棵源码树
（外加 `_source_text()` 被重复调用 3 次）——本地 Windows 实测 110.5s，占该文件总耗时的
97%，也占 CI `test (windows-latest)` 腿 pytest 总时长的约 7%。
`test_usage_never_reparses_the_source_tree` 是这件事的守卫。
"""

from __future__ import annotations

import ast
from functools import lru_cache
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[3] / "src" / "omnicrawler"

#: 必须有真实消费方的 token（守卫 / 配置项）。守卫用例与性能守卫共用同一份清单，
#: 避免两处漂移；注释保留原始出处编号。
TOKENS = (
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
)


@lru_cache(maxsize=1)
def _source_text() -> str:
    """整仓 Python 源码拼接文本。缓存保证 rglob + 读取只发生一次。"""
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


@pytest.fixture(scope="module")
def source_tree(source: str) -> ast.AST | None:
    """全仓源码 AST，module 级只解析一次。

    语法错误时返回 `None`——与原实现「解析失败即视为零消费」的语义一致：
    用例仍然**失败**并给出「无消费方」诊断，而不是退化成 fixture 错误。
    """
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


@pytest.fixture(scope="module")
def doc_nodes(source_tree: ast.AST | None) -> frozenset[int]:
    """docstring 常量节点 id 集合。树在本 module 内常驻 ⇒ 节点 id 稳定有效。"""
    if source_tree is None:
        return frozenset()
    return frozenset(_docstring_nodes(source_tree))


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


def _usage(tree: ast.AST | None, doc_nodes: frozenset[int], token: str) -> int:
    """统计 token 在**已解析**源码树中的真实引用次数（AST，排除注释与 docstring）。

    原行扫描会把内联注释/docstring 续行/字符串里的同名符号误计为消费方，
    导致「零消费即红」守卫可被纯文档符号绕过（P1-4）。AST 统计：
    - ast.Name / ast.Attribute（标识符引用，含函数/属性/类名）
    - 非 docstring 的字符串常量（覆盖 value_pattern 等配置键字面量）

    树与 docstring 集合由 `source_tree` / `doc_nodes` fixture 提供 ⇒ 本函数**不得**
    再自行 `ast.parse`，否则 11 个 token 会各自重解析一遍整仓源码。
    """
    if tree is None:
        return 0
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


@pytest.mark.parametrize("token", TOKENS)
def test_guard_or_config_has_consumer(
    source_tree: ast.AST | None, doc_nodes: frozenset[int], token: str,
) -> None:
    assert _usage(source_tree, doc_nodes, token) >= 1, f"{token} 无消费方（零消费孤儿）"


def test_usage_never_reparses_the_source_tree(monkeypatch: pytest.MonkeyPatch) -> None:
    """性能守卫：`_usage` 只能消费传入的树，不得自行 `ast.parse`。

    反向断言：把 `ast.parse(...)` 放回 `_usage` 后本用例必须转红；
    若仍为绿，说明守卫没有落在真正的重解析路径上。

    故意用**极小合成树**而非整仓 `source_tree`：被断言的性质（不重复解析）与树的大小
    无关，用小树可让守卫自身接近零成本，不必再 walk 一遍 417 个文件的 AST。
    """
    calls: list[int] = []
    real_parse = ast.parse

    def counting_parse(*args: Any, **kwargs: Any) -> ast.AST:
        calls.append(1)
        return real_parse(*args, **kwargs)

    tiny_tree = real_parse("value_pattern = None")  # 用真实 parse 构造，不计入 calls
    monkeypatch.setattr(ast, "parse", counting_parse)
    for token in TOKENS:
        _usage(tiny_tree, frozenset(), token)
    assert calls == [], (
        f"`_usage` 重复解析了 {len(calls)} 次；应只消费 source_tree fixture 提供的树"
    )


def test_deprecated_archives_still_importable() -> None:
    from omnicrawler.fetching.archives import ArchiveLimits, safe_extract_archive  # noqa: F401

    assert callable(safe_extract_archive)
    assert callable(ArchiveLimits)
    assert "已废弃" in _source_text()


def test_experimental_components_are_marked() -> None:
    text = _source_text()
    assert "实验性" in text  # AIGraphExtractor/ProxyRotator/apply_to_playwright_context 标注存在
    assert "已废弃" in text  # archives.py deprecated 标注存在
