"""`pii_summary` 的**必要性预筛**必须与逐条跑正则结果一致（2026-11-28）。

## 为什么这条测试必须逐条比对

预筛是**性能优化**，正确性判据只有一个：**结果不许变**。
所以这里不测「快了多少」（那是基准测试的事），只测「一条都不许算错」。

## 预筛的保守性

| 类别 | 预筛 | 为什么保守 |
|---|---|---|
| email | 文本含 `@` | 正则里有字面 `@`，没有就必空 |
| phone | len ≥ 11 | 最短可匹配形态是 11 位数字 |
| 身份证 | len ≥ 18 | 正则要求 18 位 |

三条都是「预筛为假 ⇒ 正则必为空」，因此**不可能**漏掉命中。
"""

from __future__ import annotations

import re

import pytest

from omnicrawler.security.security_audit import pii_summary

#: 旧实现（无预筛）的等价物 —— 作为**参照答案**。
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE = re.compile(r"(?<!\d)(?:\+?86[- ]?)?1[3-9]\d{9}(?!\d)")
_ID_CARD = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")


def _reference(records: list[dict]) -> dict[str, int]:
    """逐条跑全部正则，不做任何预筛。"""
    patterns = {"email": _EMAIL, "phone": _PHONE, "id_card_candidate": _ID_CARD}
    counts = {name: 0 for name in patterns}
    for record in records:
        text = " ".join(str(value) for value in record.values() if value is not None)
        for name, pattern in patterns.items():
            counts[name] += len(pattern.findall(text))
    return counts


_SAMPLES: list[dict] = [
    {"body": "contact me at alice@example.com please"},
    {"body": "call 13812345678 now"},
    {"body": "+8613812345678"},
    {"body": "86 13812345678"},
    {"body": "110101199003071234"},
    {"body": "id: 11010119900307123X"},
    {"body": "nothing here at all"},
    {"body": ""},
    {"body": "x"},
    {"title": "Doc", "author": "bob@corp.co.uk", "body": "lorem ipsum"},
    {"body": "a@b.co"},
    {"body": "1234567890123456789012345"},
    {"body": "mixed alice@example.com and 13812345678 together"},
    {"body": None, "author": "carol@example.org"},
    {"body": 12345},
]


@pytest.mark.parametrize("record", _SAMPLES)
def test_single_record_matches_reference(record: dict) -> None:
    """★ 逐条比对：预筛版与参照实现必须完全相同。"""
    assert pii_summary([record]) == _reference([record])


def test_batch_matches_reference() -> None:
    """★ 整批比对（覆盖「预筛在某条上判错但被其他条抵消」的情形）。"""
    assert pii_summary(_SAMPLES) == _reference(_SAMPLES)


def test_empty_input_is_zeroed_not_omitted() -> None:
    """★ 空输入返回三个 0 键 —— 不是空字典。

    空字典会让消费方 `report["email"]` 直接 KeyError。
    """
    assert pii_summary([]) == {"email": 0, "phone": 0, "id_card_candidate": 0}


def test_short_text_with_full_phone_still_counts() -> None:
    """★ 边界：恰好 11 位（手机号最短形态）必须命中。

    `len >= 11` 的预筛若写成 `> 11` 就会漏掉这条。
    """
    assert pii_summary([{"body": "13812345678"}])["phone"] == 1


def test_exactly_18_digit_id_card_still_counts() -> None:
    """★ 边界：恰好 18 位（身份证最短形态）必须命中。"""
    assert pii_summary([{"body": "110101199003071234"}])["id_card_candidate"] == 1


def test_email_requires_the_at_sign_prefilter() -> None:
    """★ email 预筛只看 `@`：没有 `@` 的文本不跑 email 正则，结果仍为 0。"""
    assert pii_summary([{"body": "alice.example.com"}])["email"] == 0
    assert pii_summary([{"body": "alice@example.com"}])["email"] == 1
