"""JSONPath 子集引擎 + 在线验证（批 A-1）。

设计决策（A-1）：
- 提取引擎（``extraction.extractors.json_path``）实际支持的子集为：
  ``$`` 根、``.key`` 属性、``[n]`` 数组索引、``[*]`` 数组展开（裸 key 亦允许，
  兼容 ``sources`` 内部的 ``links.next.href`` 用法）。
- 帮助文档宣传的 ``..`` 递归、``[?(...)]`` 过滤、``[a:b]`` 切片当前**不被引擎支持**
  （引擎静默按 key 导航，结果为空却无提示）。本模块的 compile_path 对这些语法
  显式抛出 JsonPathSyntaxError，让「在线验证」如实反映引擎能力，避免
  「验证通过但提取为空」的困惑。
- 求值逻辑与提取引擎逐 token 等价（见 test_jsonpath 的等价性用例），
  本模块**不迁移**提取引擎（最小改动），仅提供严格校验 + 样本验证。
- 无第三方依赖（不引 jmespath），自包含。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .safe_data import safe_json_loads


class JsonPathSyntaxError(ValueError):
    """JSONPath 语法错误（含人类可读的修复提示）。"""


@dataclass(slots=True)
class JsonPathValidation:
    """单次验证结果。

    Attributes:
        ok: 语法是否通过（样本 JSON 无效时亦为 False）。
        error: ok=False 时的错误说明。
        matches: 有样本且语法通过时，匹配条数；无样本时为 None。
        sample_values: 有样本时最多 max_samples 条匹配值。
        path: 被验证的表达式。
    """

    ok: bool
    path: str = ""
    error: str = ""
    matches: int | None = None
    sample_values: list[Any] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "path": self.path,
            "error": self.error,
            "matches": self.matches,
            "sample_values": self.sample_values,
        }


def compile_path(path: str) -> tuple[str, ...]:
    """严格分词 + 语法校验。

    Returns:
        token 序列（字符串 token；"*" 为通配；全数字串运行时按数组索引解析，
        与提取引擎一致——dict 的数字键仍可命中）。

    Raises:
        JsonPathSyntaxError: 语法不支持或非法（含位置与修复提示）。
    """
    raw = path.strip()
    if raw in {"", "$", "."}:
        return ()
    if raw.startswith("$"):
        body = raw[1:]
    else:
        body = raw
    if body.startswith(".."):
        raise JsonPathSyntaxError("递归搜索 '..' 暂不支持，请改用 '.' 逐层导航（如 $.data.list[0].title）")
    tokens: list[str] = []
    index = 0
    length = len(body)
    while index < length:
        ch = body[index]
        if ch == ".":
            if index + 1 >= length:
                raise JsonPathSyntaxError("路径以 '.' 结尾，缺少字段名")
            if body[index + 1] == ".":
                raise JsonPathSyntaxError("递归搜索 '..' 暂不支持，请改用 '.' 逐层导航")
            index += 1
            continue
        if ch == "[":
            end = body.find("]", index + 1)
            if end < 0:
                raise JsonPathSyntaxError("缺少 ']'：方括号索引未闭合")
            inner = body[index + 1 : end]
            if inner == "*":
                tokens.append("*")
            elif inner.isdigit():
                tokens.append(inner)
            elif ":" in inner:
                raise JsonPathSyntaxError("数组切片 '[a:b]' 暂不支持，请用 '[n]' 单索引")
            elif inner.startswith("?"):
                raise JsonPathSyntaxError("过滤条件 '[?(...)]' 暂不支持")
            else:
                raise JsonPathSyntaxError(f"方括号内仅支持数字索引或 '*': [{inner}]")
            index = end + 1
            continue
        if ch == "]":
            raise JsonPathSyntaxError("意外的 ']'，请检查方括号配对")
        start = index
        while index < length and body[index] not in ".[]":
            index += 1
        token = body[start:index]
        if token:
            tokens.append(token)
    return tuple(tokens)


def json_path(value: Any, path: str) -> list[Any]:
    """按 JSONPath 子集求值，语义与 extraction.extractors.json_path 逐 token 等价。

    根路径（"" / "$" / "."）返回 ``[value]``；无匹配返回空列表（不抛异常）。
    """
    tokens = compile_path(path)
    current: list[Any] = [value]
    for token in tokens:
        next_values: list[Any] = []
        if token == "*":
            for item in current:
                if isinstance(item, list):
                    next_values.extend(item)
        elif token.isdigit():
            index = int(token)
            for item in current:
                if isinstance(item, list) and 0 <= index < len(item):
                    next_values.append(item[index])
                elif isinstance(item, dict) and token in item:
                    # B05-026：数字 token 对 list 按索引、对 dict 按数字键双命中——
                    # 与既有引擎语义一致，属预期行为，非 bug。
                    next_values.append(item[token])  # dict 的数字键（与引擎一致）
        else:
            for item in current:
                if isinstance(item, dict) and token in item:
                    next_values.append(item[token])
        current = next_values
    return current


def validate(
    expression: str,
    sample: Any = None,
    *,
    max_samples: int = 5,
) -> JsonPathValidation:
    """在线验证：语法校验 + 可选样本试运行。

    Args:
        expression: JSONPath 表达式。
        sample: 待验证样本——已解析对象（dict/list）或 JSON 字符串/bytes。
            为 None 时仅做语法校验。
        max_samples: 结果中保留的最大匹配值条数。

    Returns:
        JsonPathValidation。
    """
    result = JsonPathValidation(ok=False, path=expression)
    try:
        compile_path(expression)
    except JsonPathSyntaxError as exc:
        result.error = str(exc)
        return result
    result.ok = True
    if sample is None:
        return result
    payload = sample
    if isinstance(sample, (str, bytes)):
        payload = safe_json_loads(sample, default=None)
        if payload is None:
            result.ok = False
            result.error = "JSON 样本无法解析，请检查粘贴内容是否为合法 JSON"
            return result
    values = json_path(payload, expression)
    result.matches = len(values)
    result.sample_values = values[:max_samples]
    return result


def describe_syntax() -> str:
    """支持范围说明（供 GUI 验证对话框与帮助页展示）。"""
    return (
        "支持语法:\n"
        "  $             根对象\n"
        "  .key          访问属性（$.data.title）\n"
        "  [n]           数组索引（$.list[0]）\n"
        "  [*]           全部数组元素（$.list[*].title）\n"
        "暂不支持（验证会报错）:\n"
        "  .. 递归搜索、[?(...)] 过滤条件、[a:b] 切片、['key'] 引号键"
    )


__all__ = [
    "JsonPathSyntaxError",
    "JsonPathValidation",
    "compile_path",
    "json_path",
    "validate",
    "describe_syntax",
]
