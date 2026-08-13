"""AST 白名单求值器（core/ast_evaluator.py）单元测试。

覆盖：算术/一元正负/比较逻辑/三元/容器下标/白名单函数；
拒绝 Attribute（魔法链）、未允许函数、推导式；builtins 双保险锁死。
"""

from __future__ import annotations

import pytest

from omnicrawl.core.ast_evaluator import ALLOWED_FUNCTIONS, safe_eval


def test_arithmetic_and_unary() -> None:
    assert safe_eval("2 + 3 * 4", {}) == 14
    assert safe_eval("-5 + 3", {}) == -2  # USub
    assert safe_eval("+5", {}) == 5  # UAdd
    assert safe_eval("-(2 + 3)", {}) == -5
    assert safe_eval("2 ** 3", {}) == 8
    assert safe_eval("7 // 2", {}) == 3


def test_compare_logic_ternary() -> None:
    assert safe_eval("1 if x > 2 else 0", {"x": 5}) == 1
    assert safe_eval("1 if x > 2 else 0", {"x": 1}) == 0
    assert safe_eval("a and b or c", {"a": True, "b": False, "c": "fallback"}) == "fallback"
    assert safe_eval("'x' in lst", {"lst": ["x", "y"]}) is True


def test_list_dict_subscript() -> None:
    assert safe_eval("d['k'] + '!'", {"d": {"k": "v"}}) == "v!"
    assert safe_eval("nums[1:3]", {"nums": [1, 2, 3, 4]}) == [2, 3]
    assert safe_eval("t[0] + t[1]", {"t": (1, 2)}) == 3


def test_whitelisted_functions() -> None:
    assert safe_eval("trim(title) + '|' + parse_number(price)", {"title": "  x ", "price": "1.50"}) == "x|1.5"
    assert safe_eval("coalesce(a, b)", {"a": "", "b": "second"}) == "second"
    assert safe_eval("clean_html('<p>a &amp; b</p>')", {}) == "a & b"
    assert safe_eval("regex_extract(s, r'-(\\d+)')", {"s": "abc-123"}) == "123"


def test_parse_money_unresolved_returns_original() -> None:
    assert safe_eval("parse_money(v)", {"v": "不是金额"}) == "不是金额"


def test_rejects_attribute_call() -> None:
    # getattr(node.func, "id", None) → None → 拒绝
    with pytest.raises(ValueError):
        safe_eval("'abc'.upper()", {})


def test_rejects_magic_chain() -> None:
    # Attribute 节点整体禁止，__class__ 魔法链不可达
    with pytest.raises(ValueError):
        safe_eval("().__class__", {})


def test_rejects_unknown_function() -> None:
    with pytest.raises(ValueError):
        safe_eval("len(x)", {"x": "abc"})
    with pytest.raises(ValueError):
        safe_eval("__import__('os')", {})


def test_rejects_comprehension() -> None:
    with pytest.raises(ValueError):
        safe_eval("[i for i in range(3)]", {})


def test_rejects_lambda_and_assignment() -> None:
    with pytest.raises(ValueError):
        safe_eval("lambda x: x", {})
    with pytest.raises(ValueError):
        safe_eval("x = 1", {})


def test_builtins_locked() -> None:
    # globals/locals 均显式 {"__builtins__": {}}，禁止 CPython 自动注入
    assert safe_eval("__builtins__", {}) == {}
    with pytest.raises(ValueError):
        safe_eval("__builtins__.open('/etc/passwd')", {})


def test_syntax_error_raises_value_error() -> None:
    with pytest.raises(ValueError):
        safe_eval("1 +", {})


def test_allowlist_contains_only_public_wrappers() -> None:
    assert set(ALLOWED_FUNCTIONS) == {
        "parse_money", "parse_time", "parse_number", "trim",
        "clean_html", "regex_extract", "coalesce", "concat",
    }
