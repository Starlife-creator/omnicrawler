"""B-P3-9：AST 表达式静态规模边界——节点总数上限 + 幂运算常量底数/指数上限。"""

from __future__ import annotations

import pytest

from omnicrawler.core.ast_evaluator import safe_eval


def test_small_power_still_allowed() -> None:
    assert safe_eval("2 ** 3", {}) == 8


def test_large_power_exponent_rejected() -> None:
    with pytest.raises(ValueError, match="指数超出规模上限"):
        safe_eval("2 ** 100000", {})


def test_large_power_base_rejected() -> None:
    with pytest.raises(ValueError, match="底数超出规模上限"):
        safe_eval("1000000000 ** 2", {})


def test_variable_power_not_rejected_statically() -> None:
    # 操作数来自字段值，静态阶段不拦截；求值结果由调用方上下文约束。
    assert safe_eval("x ** y", {"x": 2, "y": 3}) == 8


def test_oversized_expression_node_count_rejected() -> None:
    expression = "1" + " + 1" * 300
    with pytest.raises(ValueError, match="节点数超出静态上限"):
        safe_eval(expression, {})
