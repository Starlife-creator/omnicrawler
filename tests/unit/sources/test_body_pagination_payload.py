"""`location: body` 的**运行时兜底**：把裸异常换成可行动报错（`sources.py`）。

正常情况下用户到不了这里 —— `validate_config` 会在**加载时**就拦住
（见 `tests/unit/config/test_body_location_payload_must_be_mapping.py`，CLI/GUI 共用入口）。
本文件测的是**绕过配置校验**的情形（程序化构造 `AppConfig`、插件直连等）：

* 此前是 `dict(self.source.get("payload", {}))` ⇒ 非映射真值（如字符串）会抛
  **没有上下文**的 `ValueError`；现在改为指名道姓，说明是哪条配置、期望什么、实际什么。
* 另一个崩溃点：`payload:`（显式 `null`）⇒ `get` 返回 `None` ⇒ `dict(None)` 抛 `TypeError`；
  现在与"没有请求体"等价，页码照常进请求体。

★ 这两条都是**实测**出来的（不是推理）：判据与文案的唯一真源是
  `core.pagination.body_location_payload_error`。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from omnicrawler.core.config import AppConfig
from omnicrawler.sources.sources import GenericSource

_SEEDS = [{"url": "https://api.example.com/items", "method": "POST"}]
_PAGINATION = {"type": "page", "parameter": "offset", "location": "body", "start": 1, "end": 2}


def _config(*, payload: object = None, with_payload: bool = False) -> AppConfig:
    """直接构造配置对象，**绕开** `validate_config`（这正是本文件要覆盖的路径）。"""
    source: dict = {"kind": "rest", "seeds": _SEEDS, "pagination": _PAGINATION}
    if with_payload:
        source["payload"] = payload
    raw = {
        "project": {"name": "t", "workspace": "work"},
        "source": source,
    }
    return AppConfig(path=Path("p.yaml"), root=Path("."), raw=raw, workspace=Path("work"))


def test_non_mapping_payload_raises_actionable_error() -> None:
    """字符串 payload：报错必须**点名** `source.payload`，而不是裸 `dict()` 的异常。"""
    with pytest.raises(ValueError) as caught:
        GenericSource(_config(payload="raw-body-text", with_payload=True)).seed()
    message = str(caught.value)
    assert "source.payload" in message, message
    assert "str" in message, message


def test_null_payload_is_treated_as_no_body() -> None:
    """`payload: null` 不再崩：与"没有请求体"等价，页码照常并入请求体。"""
    requests = GenericSource(_config(payload=None, with_payload=True)).seed()
    assert [json.loads(item.body) for item in requests] == [{"offset": 1}, {"offset": 2}]


def test_mapping_payload_is_merged_with_the_page_parameter() -> None:
    """映射 payload：原有语义不许被改动（并入页码，不是覆盖）。"""
    requests = GenericSource(_config(payload={"size": 10}, with_payload=True)).seed()
    assert [json.loads(item.body) for item in requests] == [
        {"size": 10, "offset": 1},
        {"size": 10, "offset": 2},
    ]


def test_absent_payload_still_paginates_in_body() -> None:
    """缺省 payload：映射缺省为 `{}`（这是 runtime 一直支持的用法）。"""
    requests = GenericSource(_config()).seed()
    assert [json.loads(item.body) for item in requests] == [{"offset": 1}, {"offset": 2}]
