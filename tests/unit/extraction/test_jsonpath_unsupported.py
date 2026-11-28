"""JSONPath 引擎：不支持的语法必须**显式拒绝**，不许静默降级（2026-11-28）。

## 缺陷

`json_path()` 的分词正则会跳过 `..`，于是 `$..author` 被当成 `$.author` ——
只命中**顶层** author，而不是递归命中所有层。

```python
sample = {"data": {"author": {"name": "nested"}}, "author": {"name": "top"}}
json_path(sample, "$..author")   # -> [{"name": "top"}]        只命中顶层
json_path(sample, "$.author")    # -> [{"name": "top"}]        完全一样
```

## 为什么这比返回空更糟

返回空时用户会怀疑「路径写错了」，去查文档。返回**非空但内容缺失**时，
用户看到有结果就继续跑完了 —— 数据少了几层，直到导出后才发现，而那时已经重跑过。

## 更糟的是内部不一致

`core/jsonpath.py` 的 `compile_path()` 一直明确拒绝 `..` 与 `[?(...)]`，
因此「校验器说不支持、引擎却给出结果」这件事本身就在骗人。
"""

from __future__ import annotations

import pytest

from omnicrawler.extraction.extractors import json_path

_SAMPLE = {
    "data": {"author": {"name": "nested"}, "items": [{"title": "A"}]},
    "author": {"name": "top"},
}


def test_recursive_descent_is_rejected() -> None:
    """★ 核心判据：`$..author` 必须报错，不能静默只命中顶层。"""
    with pytest.raises(ValueError, match="递归搜索"):
        json_path(_SAMPLE, "$..author")


def test_filter_expression_is_rejected() -> None:
    """`[?(...)]` 引擎不支持 —— 此前静默返回空列表。"""
    with pytest.raises(ValueError, match="过滤条件"):
        json_path(_SAMPLE, "$.data.items[?(@.title)]")


def test_error_message_contains_a_working_alternative() -> None:
    """★ 报错要给**能用的**替代写法，不能只说「不支持」。"""
    with pytest.raises(ValueError) as excinfo:
        json_path(_SAMPLE, "$..author")
    message = str(excinfo.value)
    assert "逐层导航" in message
    assert "$." in message, "提示里应给出可照抄的路径示例"


def test_error_names_the_offending_path() -> None:
    """报文里要带上用户写的那条路径 —— 否则不知道是哪条规则出错。"""
    with pytest.raises(ValueError) as excinfo:
        json_path(_SAMPLE, "$..author")
    assert "$..author" in str(excinfo.value)


def test_supported_paths_still_work() -> None:
    """★ 加了拒绝逻辑不许误伤正常路径。"""
    assert json_path(_SAMPLE, "$.author") == [{"name": "top"}]
    assert json_path(_SAMPLE, "$.data.author.name") == ["nested"]
    assert json_path(_SAMPLE, "$.data.items[*].title") == ["A"]
    assert json_path(_SAMPLE, "$.data.items[0].title") == ["A"]


def test_root_paths_still_work() -> None:
    for root in ("", "$", "."):
        assert json_path(_SAMPLE, root) == [_SAMPLE]


def test_bare_keys_still_work() -> None:
    """裸 key（无 `$`）是既有用法（`sources` 的 `links.next.href`）。"""
    assert json_path(_SAMPLE, "author") == [{"name": "top"}]
    assert json_path(_SAMPLE, "data.author.name") == ["nested"]


def test_missing_key_still_returns_empty() -> None:
    """★ 关键区别：**缺失字段**仍返回空（那是正常语义），

    只有**不支持的语法**才报错。两者不能混为一谈 ——
    把「缺失」也改成报错会让所有可选字段路径失效。
    """
    assert json_path(_SAMPLE, "$.no_such_key") == []
    assert json_path(_SAMPLE, "$.data.no_such_key.deeper") == []


def test_engine_and_validator_agree_on_unsupported_syntax() -> None:
    """★ 这是缺陷的根源：两边必须给出**一致**的判定。

    此前 `compile_path()` 拒绝、`json_path()` 静默给结果 —— 用户被夹在中间。
    """
    from omnicrawler.core.jsonpath import JsonPathSyntaxError, compile_path

    for path in ("$..author", "$.data.items[?(@.x>1)]"):
        with pytest.raises(JsonPathSyntaxError):
            compile_path(path)
        with pytest.raises(ValueError):
            json_path(_SAMPLE, path)
