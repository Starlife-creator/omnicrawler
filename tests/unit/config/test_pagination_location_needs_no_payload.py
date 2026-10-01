"""`location: body` **不需要** `source.payload` 配套 —— 一条「反判据」，钉住"别发明前置条件"。

背景（2026-09-30）：本轮为了让 `location` 能在表单里选，曾顺手加了一条跨字段判据
「`location: body` 必须有非空 `source.payload`」，**在 CI 上被实测证伪**：

* 运行时 `sources.py::GenericSource.seed` 写的是
  ``if template.method == "POST" and pagination.get("location") == "body":``
  ``    payload = dict(self.source.get("payload", {})); payload[name] = page``
  ⇒ `payload` **缺省即 `{}`**，页码照样进请求体 ⇒ 它**不是**前置条件；
  ⇒ 真正的开关是**该种子请求的 `method` 为 `POST`**；不是 POST 时这一支不走，页码照旧
    拼到 URL query（`_with_query`）—— **分页仍然工作**，只是位置不是 body，
    不构成"请求照发却抓不到数据"。
* `payload` 是**映射**（如 `{"size": 10}`）而不是只字符串 ⇒ 按「字符串非空」判它会**假红**：
  `tests/integration/browser/test_sources.py::test_page_pagination_get_and_post_body`
  就是这么被 macOS 腿抓住的。
* 契约层立场一致：`validate_pagination({"type": "page", "parameter": "p", "location": "body"})`
  本就返回 `[]`（见 `tests/unit/core/test_pagination_contract.py`）。

★ 本文件的职责＝**防止这类"发明出来的前置条件"被重新加进校验**：只要有人再写一条
  「body 必须有 payload」，下面这些用例就会红。真实前置（种子的 `method == POST`）
  写在 GUI 字段标签与 `docs/CONFIG_REFERENCE.md` 里，**不写成校验**。

★ 另记一条**已发现但本批不修**的窄缺陷（留给后续批次按证据定夺）：
  `location: body` 且 `payload` 是**非空字符串**时，运行时的 `dict(payload)` 会抛
  `ValueError`（字符串不是键值对序列）。它与本文件断言的正交——本文件只声明"payload 不是
  前置条件"，不声明"任何 payload 类型在 body 模式下都可用"。
"""

from __future__ import annotations

import copy
from pathlib import Path

from omnicrawler.core.config import AppConfig, validate_config

_BASE: dict = {
    "project": {"name": "t", "workspace": "work"},
    "source": {
        "kind": "rest",
        "seeds": ["https://api.example.com/"],
        # 真实前置：种子请求得是 POST，`location: body` 才会生效（不是 POST 就走 query）。
        "method": "POST",
    },
}


def _errors(*, pagination: dict | None = None, payload: object = None) -> list[str]:
    """返回**全部**校验错误（不过滤）。

    刻意不过滤"含 payload 的消息"：那种写法只在**已知**措辞下有效，换个说法就漏。
    下面用**差分**（body 与 query 的错误列表必须逐字相同）来表达"加 body 不引入新要求"。
    """
    raw = copy.deepcopy(_BASE)
    source = raw["source"]
    if pagination is not None:
        source["pagination"] = pagination
    if payload is not None:
        source["payload"] = payload
    config = AppConfig(path=Path("p.yaml"), root=Path("."), raw=raw, workspace=Path("work"))
    errors, _warnings = validate_config(config)
    return errors


_QUERY = {"type": "page", "parameter": "p", "location": "query"}
_BODY = {"type": "page", "parameter": "p", "location": "body"}
_BASELINE = _errors(pagination=_QUERY)


def test_body_location_adds_no_requirement_over_query() -> None:
    """★ 核心断言：把 `query` 换成 `body`，**错误列表必须逐字不变**。

    这比"断言没有错误"更强也更准 —— 基线里可能有与本判据无关的错误，差分能把它消掉，
    只留下"多出来的那条要求"（若有人重新加上「body 必须有 payload」，这里立刻不平衡）。
    """
    assert _errors(pagination=_BODY) == _BASELINE


def test_mapping_payload_adds_no_requirement() -> None:
    """payload 的真实形态是**映射**（CI 假红就是被「只认非空字符串」判出来的）。"""
    assert _errors(pagination=_BODY, payload={"size": 10}) == _BASELINE


def test_no_error_message_mentions_payload_for_body_location() -> None:
    """再加一道口语化防线：整条消息里出现 `payload` 也判红（防换措辞把同类假判据加回来）。"""
    errors = _errors(pagination=_BODY)
    assert not [error for error in errors if "payload" in error], errors


def test_location_is_editable_in_the_contract() -> None:
    """契约事实：`location` 可编辑（表单据此渲染成下拉）。

    这条与「不需要 payload」是一对：**能选**（表单可编辑）＋**不凭空设限**（没有假前置）。
    只上后者会让用户仍然只能手写 YAML；只上前者则会把一个合法选项后面挂上假报错。
    """
    from omnicrawler.core.pagination import SHAPES

    location = [f for shape in SHAPES for f in shape.fields if f.name == "location"]
    assert location, "契约里必须有 location 字段"
    assert all(f.editable for f in location), "location 应可编辑（否则 GUI 只能手写 YAML）"
    assert location[0].choices == ("query", "body"), "下拉选项来自契约，不能是第二份词典"
