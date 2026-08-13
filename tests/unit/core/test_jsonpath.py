"""JSONPath 子集引擎与在线验证（core/jsonpath.py）单元测试。

覆盖：compile_path 合法/非法语法、json_path 求值、与提取引擎等价性、
validate（样本 JSON 字符串、无效样本、匹配数与截断）。
"""

from __future__ import annotations

import pytest

from omnicrawl.core.jsonpath import (
    JsonPathSyntaxError,
    compile_path,
    describe_syntax,
    json_path,
    validate,
)

SAMPLE = {
    "data": {
        "items": [
            {"title": "文章一", "price": "12.5"},
            {"title": "文章二", "price": "30"},
            {"title": "文章三", "price": "9.9"},
        ]
    },
    "count": 3,
}


# ── compile_path 合法 ───────────────────────────────────────────


def test_compile_valid_paths() -> None:
    assert compile_path("$") == ()
    assert compile_path("") == ()
    assert compile_path("$.data") == ("data",)
    assert compile_path("data.items[*]") == ("data", "items", "*")
    assert compile_path("$.a.b[0]") == ("a", "b", "0")
    assert compile_path("$[0]") == ("0",)
    assert compile_path("links.next.href") == ("links", "next", "href")


# ── compile_path 非法语法（明确报错，区别于引擎静默失败）─────────


def test_compile_rejects_recursive_search() -> None:
    with pytest.raises(JsonPathSyntaxError, match="递归搜索"):
        compile_path("$..author")
    with pytest.raises(JsonPathSyntaxError, match="递归搜索"):
        compile_path("$.a..b")


def test_compile_rejects_filter_and_slice() -> None:
    with pytest.raises(JsonPathSyntaxError, match="过滤条件"):
        compile_path("$.items[?(@.price<100)]")
    with pytest.raises(JsonPathSyntaxError, match="切片"):
        compile_path("$.list[0:10]")


def test_compile_rejects_unclosed_and_unexpected_brackets() -> None:
    with pytest.raises(JsonPathSyntaxError, match="未闭合"):
        compile_path("$.a[0")
    with pytest.raises(JsonPathSyntaxError, match="方括号内仅支持"):
        compile_path("$.a['key']")
    with pytest.raises(JsonPathSyntaxError, match="方括号内仅支持"):
        compile_path("$.a[abc]")
    with pytest.raises(JsonPathSyntaxError, match="意外的 ']'"):
        compile_path("$.a]")


def test_compile_rejects_trailing_dot() -> None:
    with pytest.raises(JsonPathSyntaxError, match="'.' 结尾"):
        compile_path("$.a.")


# ── json_path 求值 ──────────────────────────────────────────────


def test_json_path_navigation() -> None:
    assert json_path(SAMPLE, "$.data.items[0].title") == ["文章一"]
    assert json_path(SAMPLE, "$.data.items[*].title") == ["文章一", "文章二", "文章三"]
    assert json_path(SAMPLE, "$.count") == [3]
    assert json_path(SAMPLE, "$") == [SAMPLE]
    assert json_path(SAMPLE, "") == [SAMPLE]


def test_json_path_numeric_key_on_dict() -> None:
    # 数字串 token：list 按索引，dict 按数字键（与提取引擎一致）
    assert json_path({"0": "zero", "1": "one"}, "0") == ["zero"]


def test_json_path_no_match_returns_empty() -> None:
    assert json_path(SAMPLE, "$.missing") == []
    assert json_path(SAMPLE, "$.data.items[9]") == []


# ── 与提取引擎等价性（防双实现漂移）────────────────────────────


def test_equivalence_with_extract_engine() -> None:
    from omnicrawl.extraction.extractors import json_path as engine_json_path

    paths = ["$", "$.data", "$.count", "data.items[*].title", "$.data.items[0]", "$.missing"]
    for path in paths:
        assert json_path(SAMPLE, path) == engine_json_path(SAMPLE, path), f"路径不一致: {path}"


# ── validate 在线验证 ───────────────────────────────────────────


def test_validate_syntax_only() -> None:
    result = validate("$.data.items[*].title")
    assert result.ok is True
    assert result.matches is None
    assert result.sample_values == []


def test_validate_syntax_error() -> None:
    result = validate("$..author")
    assert result.ok is False
    assert "递归搜索" in result.error


def test_validate_with_json_string_sample() -> None:
    import json as _json

    result = validate("$.data.items[*].title", _json.dumps(SAMPLE, ensure_ascii=False))
    assert result.ok is True
    assert result.matches == 3
    assert result.sample_values == ["文章一", "文章二", "文章三"]


def test_validate_with_invalid_sample() -> None:
    result = validate("$.data", "{not valid json")
    assert result.ok is False
    assert "JSON 样本无法解析" in result.error


def test_validate_max_samples_truncation() -> None:
    result = validate("$.data.items[*]", SAMPLE, max_samples=2)
    assert result.ok is True
    assert result.matches == 3
    assert len(result.sample_values) == 2


def test_describe_syntax_is_nonempty() -> None:
    assert "支持语法" in describe_syntax()
