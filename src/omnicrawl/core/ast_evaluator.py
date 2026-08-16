"""AST 白名单求值器：安全执行值级表达式（阶段 0 H4）。

设计决策（H4）：
- 只放行表达式白名单节点，其余节点一律 ValueError（白名单判据而非黑名单）。
- 双保险：eval 的 globals 与 locals 均显式传 ``{"__builtins__": {}}``，
  阻断 CPython 在 globals 缺 ``__builtins__`` 键时的自动内置注入。
- 可调用函数仅限 ALLOWED_FUNCTIONS（normalizers 公开值级包装），
  契约：无副作用、解析失败返回原值、异常不外抛。
- ast.Call 用 ``getattr(node.func, "id", None)`` 防御属性调用；
  并整体禁止 ast.Attribute 节点，杜绝 ``__class__``/``__subclasses__`` 魔法链逃逸。
- 一元 +/-（USub/UAdd）显式放行，支持 ``-x``/``+x`` 与常量折叠写法。

表达式来源：B-2 ``omnicrawl transform --map`` 用户表达式（本地可信），
白名单主要防御「配置/任务文件被第三方注入」场景。
"""

from __future__ import annotations

import ast
from collections.abc import Callable
from typing import Any

from ..quality.normalizers import (
    clean_html,
    coalesce,
    concat,
    parse_money,
    parse_number,
    parse_time,
    regex_extract,
    trim,
)

#: transform 表达式可调用的安全函数表（Key = 表达式中的函数名）
ALLOWED_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "parse_money": parse_money,
    "parse_time": parse_time,
    "parse_number": parse_number,
    "trim": trim,
    "clean_html": clean_html,
    "regex_extract": regex_extract,
    "coalesce": coalesce,
    "concat": concat,
}

_ALLOWED_NODES: frozenset[type[ast.AST]] = frozenset({
    ast.Expression,
    ast.Constant,
    ast.Name,
    ast.Load,
    # 二元运算
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    # 一元运算
    ast.UnaryOp, ast.USub, ast.UAdd, ast.Not,
    # 比较
    ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.In, ast.NotIn,
    # 逻辑与三元
    ast.BoolOp, ast.And, ast.Or,
    ast.IfExp,
    # 函数调用
    ast.Call, ast.keyword,
    # 字面量容器与下标
    ast.List, ast.Tuple, ast.Dict,
    ast.Subscript, ast.Slice,
})


def safe_eval(expression: str, variables: dict[str, Any]) -> Any:
    """求值白名单表达式。

    Args:
        expression: 值级表达式，如 ``trim(title) + " | " + parse_number(price)``。
        variables: 字段值环境（ast.Name 的解析来源）。

    Raises:
        ValueError: 语法错误，或表达式含非白名单节点 / 未允许函数。

    B05-012：变量优先于函数——context 为 ``{**ALLOWED_FUNCTIONS, **variables}``，
    字段名与函数重名时字段值生效（数据驱动，函数可被字段遮蔽）。
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"表达式语法错误: {exc}") from exc
    _validate(tree)
    context: dict[str, Any] = {**ALLOWED_FUNCTIONS, **variables}
    return eval(compile(tree, "<expr>", "eval"), {"__builtins__": {}}, context)


def _validate(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if type(node) not in _ALLOWED_NODES:
            raise ValueError(f"表达式包含不允许的节点: {type(node).__name__}")
        if isinstance(node, ast.Call):
            # 属性调用（node.func 为 ast.Attribute）时 getattr 返回 None → 拒绝
            name = getattr(node.func, "id", None)
            if name not in ALLOWED_FUNCTIONS:
                raise ValueError(f"表达式不允许调用: {name!r}")


__all__ = ["safe_eval", "ALLOWED_FUNCTIONS"]
