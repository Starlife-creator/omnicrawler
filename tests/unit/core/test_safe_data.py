from __future__ import annotations

from omnicrawl.core.safe_data import (
    safe_bool,
    safe_float,
    safe_get,
    safe_int,
    safe_json_loads,
    safe_slice,
)


def test_safe_int_handles_bad_input():
    assert safe_int("42") == 42
    assert safe_int(7.9) == 7
    assert safe_int(None) is None
    assert safe_int("abc") is None
    assert safe_int({}) is None  # type: ignore[arg-type]
    assert safe_int("abc", default=0) == 0
    assert safe_int(None, default=0) == 0


def test_safe_float_handles_bad_input():
    assert safe_float("3.25") == 3.25
    assert safe_float(None) is None
    assert safe_float("xyz") is None
    assert safe_float([1]) is None
    assert safe_float("xyz", default=0.0) == 0.0


def test_safe_json_loads_handles_bad_input():
    assert safe_json_loads('{"a": 1}') == {"a": 1}
    assert safe_json_loads('{"a": 1, "bad": undefined}') is None
    assert safe_json_loads(None) is None
    assert safe_json_loads("not json at all") is None
    assert safe_json_loads(b'[]') == []
    assert safe_json_loads(b'"x"') == "x"
    assert safe_json_loads("not json", default={}) == {}


def test_safe_json_loads_does_not_raise_for_partial_garbage():
    from json import JSONDecodeError

    mixed = "prefix {'a': 1} suffix"
    try:
        result = safe_json_loads(mixed, default="fallback")
        assert result == "fallback"
    except JSONDecodeError:
        raise AssertionError("safe_json_loads must not raise")


def test_safe_get_filters_type_and_missing():
    data = {"a": 1, "b": "text", "nested": {"k": "v"}}
    assert safe_get(data, "a") == 1
    assert safe_get(data, "missing", default="x") == "x"
    assert safe_get(None, "a", default="n") == "n"
    assert safe_get("not a dict", "a", default="n") == "n"
    assert safe_get(data, "a", require_type=str, default="d") == "d"
    assert safe_get(data, "a", require_type=int) == 1


def test_safe_get_llm_choices_pattern():
    response = {"choices": [{"message": {"content": "hi"}}]}
    first = safe_get(response, "choices", require_type=list, default=[])
    assert safe_get(safe_slice(first, 0, 1) and first[0], "message", default={}) == {"content": "hi"}


def test_safe_slice_guards_non_sequences():
    assert safe_slice([1, 2, 3], 0, 2) == [1, 2]
    assert safe_slice("hello", 1, 3) == ["e", "l"]
    assert safe_slice(None, 0, 2) == []
    assert safe_slice(42, 0, 2) == []
    assert safe_slice({"a": 1}, 0, 2) == []


def test_safe_bool_semantics():
    assert safe_bool(True) is True
    assert safe_bool(False) is False
    assert safe_bool(1) is True
    assert safe_bool(0) is False
    assert safe_bool("yes") is True
    assert safe_bool("on") is True
    assert safe_bool("") is False
    assert safe_bool("maybe") is False
    assert safe_bool("maybe", default=True) is True
    assert safe_bool(None) is False


# ── P9-A4（B05-015）：ReDoS 启发式扩展 ───────────────────────────


def test_safe_regex_search_accepts_common_patterns():
    """常见合法模式（含非贪婪量词）必须正常匹配。"""
    from omnicrawl.core.safe_data import safe_regex_search

    assert safe_regex_search(r"<h1>(.*?)</h1>", "<h1>x</h1>") is not None
    assert safe_regex_search(r"<p>(.+?)</p>", "<p>y</p>") is not None
    assert safe_regex_search(r"[a-z]+", "abc") is not None
    assert safe_regex_search(r"(ab){2}", "abab") is not None
    assert safe_regex_search(r"https?://", "https://x") is not None
    assert safe_regex_search(r"\d{2,4}-\d{2}", "12-34") is not None


def test_safe_regex_search_rejects_nested_quantifier():
    """嵌套量词 (a+)+ 被拒绝（ReDoS 高危）。"""
    from omnicrawl.core.safe_data import safe_regex_search

    assert safe_regex_search(r"(a+)+", "aaaa") is None


def test_safe_regex_search_rejects_wide_alternation():
    """大交替重复 (a|b|c){n} 被拒绝。"""
    from omnicrawl.core.safe_data import safe_regex_search

    assert safe_regex_search(r"(a|b|c){2,}", "ab") is None


def test_safe_regex_search_rejects_stacked_quantifier():
    """叠加量词 a++ / a{1,3}{2} 被拒绝。"""
    from omnicrawl.core.safe_data import safe_regex_search

    assert safe_regex_search(r"a++", "aaaa") is None
    assert safe_regex_search(r"a{1,3}{2}", "aa") is None
    assert safe_regex_search(r"a*+", "aaaa") is None


def test_safe_regex_search_returns_none_on_compile_error():
    from omnicrawl.core.safe_data import safe_regex_search

    assert safe_regex_search(r"(", "x") is None
