"""`pagination.location: body` **必须**有 `source.payload` 配套（跨字段判据）。

为什么必须有（2026-09-30）：`location` 从"表单不渲染"改为**可编辑** ⇒ 用户能在 GUI 里选 `body`。
但若没有 `payload`，分页参数**无处可发** ⇒ 请求照发、运行**成功**、却抓不到数据
（本仓最忌讳的"异常伪装成功"）。所以"选得动"必须同时"报得出来"。

★ 判据只留一处：`core/config.validate_config` 是 CLI 与 GUI **共用**的配置校验入口 ⇒
两个入口自动同时获得这条保护，不必各写一遍（GUI 那边因此**一行都不用改**）。
"""

from __future__ import annotations

import copy
from pathlib import Path

from omnicrawler.core.config import AppConfig, validate_config

_BASE: dict = {
    "project": {"name": "t", "workspace": "work"},
    "source": {"kind": "rest", "seeds": ["https://api.example.com/"]},
}


def _errors(*, pagination: dict | None = None, payload: object = None) -> list[str]:
    raw = copy.deepcopy(_BASE)
    source = raw["source"]
    if pagination is not None:
        source["pagination"] = pagination
    if payload is not None:
        source["payload"] = payload
    config = AppConfig(
        path=Path("p.yaml"), root=Path("."), raw=raw, workspace=Path("work")
    )
    errors, _warnings = validate_config(config)
    return [error for error in errors if "payload" in error]


def test_body_location_without_payload_is_an_error() -> None:
    """★ 反向断言：选了 `body` 却没写请求体 ⇒ 必须**明确报错**（而不是静默发一个错请求）。"""
    errors = _errors(pagination={"type": "page", "parameter": "p", "location": "body"})
    assert errors, "应报出「location=body 需要 payload」"
    assert "source.payload" in errors[0]


def test_body_location_with_payload_passes() -> None:
    """正向：有配套的请求体 ⇒ 不报错。"""
    errors = _errors(
        pagination={"type": "page", "parameter": "p", "location": "body"},
        payload='{"filter": "ok"}',
    )
    assert errors == []


def test_query_location_does_not_require_payload() -> None:
    """反向的一半：`query`（缺省）**不**要求 payload —— 别把正常路径也拦住。"""
    assert _errors(pagination={"type": "page", "parameter": "p", "location": "query"}) == []
    assert _errors(pagination={"type": "page", "parameter": "p"}) == []


def test_blank_payload_counts_as_missing() -> None:
    """空白串等于没有：`"   "` 也要报错（否则又变成"参数无处可发"）。"""
    errors = _errors(
        pagination={"type": "page", "parameter": "p", "location": "body"}, payload="   "
    )
    assert errors, "空白 payload 应视同缺失"


def test_location_choice_is_declared_editable_in_the_contract() -> None:
    """契约事实：`location` 现在是可编辑字段（表单据此渲染下拉）。

    这条与上面几条是一对：**可编辑**（用户能选 body）＋**有配套校验**（选了必须有 payload）。
    只上其中一个都会留下洞（能选但发不出去 / 校验挡着一个谁都点不到的选项）。
    """
    from omnicrawler.core.pagination import SHAPES

    location = [
        field for shape in SHAPES for field in shape.fields if field.name == "location"
    ]
    assert location, "契约里必须有 location 字段"
    assert all(field.editable for field in location), "location 应可编辑（否则 GUI 只能手写 YAML）"
    assert location[0].choices == ("query", "body"), "下拉选项来自契约，不能是第二份词典"
