"""`location: body` 的 `source.payload` 判据：**定义一处**，且与运行时 `dict()` **逐值对齐**。

判据真源＝`core.pagination.body_location_payload_error`，它抄的是运行时原话
（`sources.py::GenericSource.seed`）：

    payload = dict(self.source.get("payload") or {})
    payload[name] = page

★ 本文件不只断言"我期望的名单"，还**把名单与 `dict()` 的真实行为对钉**：

* 判「通过」的值 ⇒ `dict(v or {})` 实测**不得抛**（否则是**假报错**，会拦住合法配置）；
* 判「报错」的值 ⇒ `dict(v)` 实测**必须抛**（否则是**假放行**，会放到运行时才崩）。

这样将来谁改了判据、与运行时脱节，这里就会红。
★ 由来：本轮先加过一条**假判据**（「body 必须有 payload」）被 CI 证伪——它拦住了一个
   payload 是**映射**的合法配置。这条教训的直接产物就是"判据必须与运行时逐值对齐"。
"""

from __future__ import annotations

import pytest

from omnicrawler.core.pagination import body_location_payload_error

#: 判「通过」的值：空值一律当"没有请求体"（`or {}`）；映射与键值对序列可被 `dict()` 消费。
ACCEPTED: list[tuple[str, object]] = [
    ("缺省", None),
    ("空串", ""),
    ("空映射", {}),
    ("非空映射", {"size": 10}),
    ("空列表", []),
    ("键值对列表", [["a", 1]]),
    ("键值对元组", (("a", 1),)),
    ("整数 0（falsy）", 0),
    ("布尔 False（falsy）", False),
    ("空集合", set()),
]

#: 判「报错」的值：真值且非映射/非键值对序列 —— 实测 `dict()` 必抛。
REJECTED: list[tuple[str, object]] = [
    ("非空字符串", "abc"),
    ("两字符字符串", "ab"),
    ("非零整数", 1),
    ("布尔 True", True),
    ("非空集合", {1}),
    ("字节串", b"ab"),
    ("任意对象", object()),
]


@pytest.mark.parametrize(("label", "value"), ACCEPTED, ids=[c[0] for c in ACCEPTED])
def test_accepted_values_pass_and_are_really_consumable(label: str, value: object) -> None:
    """判「通过」的值：不但判据说 OK，**运行时那行 `dict(v or {})` 也必须真的不抛**。"""
    assert body_location_payload_error(value) is None, f"{label} 不该被判错"
    dict(value or {})  # 不抛即通过（与运行时同一表达式）


@pytest.mark.parametrize(("label", "value"), REJECTED, ids=[c[0] for c in REJECTED])
def test_rejected_values_error_and_really_would_crash(label: str, value: object) -> None:
    """判「报错」的值：判据必须报错，**且 `dict(v)` 实测确实会抛**（证明不是假报错）。"""
    issue = body_location_payload_error(value)
    assert issue, f"{label} 应被判错（否则运行时会崩）"
    with pytest.raises((TypeError, ValueError)):
        dict(value)  # type: ignore[arg-type]


def test_message_is_actionable() -> None:
    """报错要**指名道姓**：点出配置路径、期望类型、实际类型。"""
    issue = body_location_payload_error("abc")
    assert issue is not None
    assert "source.payload" in issue
    assert "str" in issue, "要说清实际拿到的是什么类型"
    assert "location=body" in issue, "要说清是哪条配置要求的"
