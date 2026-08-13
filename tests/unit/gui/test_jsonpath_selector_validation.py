"""A-1：GUI 校验器 JSONPath 语法级校验测试。

validate_selector_format 对 jsonpath 类型从「仅 $ 前缀」升级为
core/jsonpath.compile_path 严格语法校验（与在线验证引擎一致）。
"""

from __future__ import annotations

from omnicrawl.gui.core.config_model import FieldDef
from omnicrawl.gui.core.validator import validate_selector_format


def _field(selector: str) -> FieldDef:
    return FieldDef(name="title", selector=selector, selector_type="jsonpath")


def test_jsonpath_valid_syntax_passes() -> None:
    assert validate_selector_format(_field("$.data.items[*].title")) == []


def test_jsonpath_rejects_unsupported_recursive_syntax() -> None:
    errors = validate_selector_format(_field("$.data..title"))
    assert any("语法错误" in item and "递归搜索" in item for item in errors)


def test_jsonpath_rejects_unclosed_bracket() -> None:
    errors = validate_selector_format(_field("$.data.items[0"))
    assert any("语法错误" in item for item in errors)


def test_jsonpath_requires_dollar_prefix() -> None:
    errors = validate_selector_format(_field("data.items[*]"))
    assert any("应以 $ 开头" in item for item in errors)
