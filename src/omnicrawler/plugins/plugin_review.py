"""审核辅助分析（Phase 3 Q4/G3：AI 增强审核员，AI 不审核、AI 增强审核员）。

纯静态 AST 分析产出结构化输入（能力面/导入图/危险调用/外传模式），供
LLM 与人工复核使用——**辅助输入，非门禁**；UI 规范零 Pass/Fail 按钮 +
签字物理隔离由审核端保证，本模块只产出证据。
"""

from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# 危险调用（与主仓 _preflight 同族；审核辅助标记而非拒绝）
_DANGEROUS_CALLS = (
    "eval", "exec", "compile", "__import__", "system", "popen",
    "subprocess", "pickle.loads", "marshal.loads",
)
# 数据外传模式（J2）：读取 records 的能力调用
_RECORDS_CAPABILITIES = ("records.read", "records.write")


@dataclass(frozen=True, slots=True)
class ReviewAnalysis:
    path: str
    contract_shape: int
    capabilities_called: tuple[str, ...]      # omnicrawler_sdk.call 的操作
    imports: tuple[str, ...]                   # 顶层模块名
    dangerous_calls: tuple[str, ...]           # 命中危险调用名单的调用
    record_ops: tuple[str, ...]                # records.* 能力操作（外传模式信号）
    has_network_fetch: bool                    # 是否调用 network.fetch
    metadata_fields: tuple[str, ...]           # PLUGIN_METADATA 键集合

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def review_analyze(path: Path) -> ReviewAnalysis:
    """静态审核辅助分析（不执行插件代码）。"""
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    contract_shape = 0
    has_handle = any(isinstance(n, ast.FunctionDef) and n.name == "handle" for n in tree.body)
    has_register = any(isinstance(n, ast.FunctionDef) and n.name == "register" for n in tree.body)
    if has_handle:
        contract_shape = 2
    elif has_register:
        contract_shape = 1

    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".")[0])

    capabilities: list[str] = []
    dangerous: list[str] = []
    record_ops: list[str] = []
    has_network_fetch = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            # omnicrawler_sdk.call('cap', ...) 提取能力操作
            if isinstance(node.func, ast.Attribute) and node.func.attr == "call":
                if node.args and isinstance(node.args[0], ast.Constant):
                    cap = str(node.args[0].value)
                    capabilities.append(cap)
                    if cap.startswith("records."):
                        record_ops.append(cap)
                    if cap == "network.fetch":
                        has_network_fetch = True
            # 危险调用
            func_name = _call_name(node.func)
            if func_name in _DANGEROUS_CALLS:
                dangerous.append(func_name)

    metadata_fields: tuple[str, ...] = ()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "PLUGIN_METADATA":
                    try:
                        value = ast.literal_eval(node.value)
                        if isinstance(value, dict):
                            metadata_fields = tuple(sorted(value))
                    except (ValueError, TypeError):
                        pass

    return ReviewAnalysis(
        path=str(path.resolve()),
        contract_shape=contract_shape,
        capabilities_called=tuple(sorted(set(capabilities))),
        imports=tuple(sorted(imports)),
        dangerous_calls=tuple(sorted(set(dangerous))),
        record_ops=tuple(sorted(set(record_ops))),
        has_network_fetch=has_network_fetch,
        metadata_fields=metadata_fields,
    )


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_call_name(node.value)}.{node.attr}" if node.value else node.attr
    return None
