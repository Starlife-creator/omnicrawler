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
