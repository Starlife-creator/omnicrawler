"""字段级 `collapse_whitespace`：OCR 空白归一是**显式选项**，默认关。

背景（实测）：`chi_sim` 会在汉字之间插空格（"示例服务合同" → "示例  服务  合同"），
而取值模式 `(?P<value>[^\\n]+)` 会把整行余下内容连同空格一起收进来 ⇒ 空格留在值里。
按用户裁决（方案 C）**不改默认产出**，只提供显式开关；原始值仍在 "…_原始值" 与原文证据里。

本文件锁三件事：
1. 默认关 ⇒ 文本值原样（只去首尾空白），既有产出不变；
2. 显式开 ⇒ 连续空白折叠，且**只删"汉字紧邻汉字"**那一个空格（英文/数字之间的空白必须保留）；
3. 配在数值/日期类字段上必须**报错**，不允许静默失效（沿用该文件 D26/D27 的既有原则）。
"""

from __future__ import annotations

import pytest

from omnicrawler.pdfx.config import FieldSpec
from omnicrawler.pdfx.normalization import normalize_value

#: OCR 会在汉字之间插空格 —— 这是本选项要解决的噪声
OCR_CJK_TEXT = "示例  服务  合同"


def _spec(**overrides) -> FieldSpec:
    return FieldSpec.from_dict({"name": "party", "label": "当事方", "type": "text", **overrides})


def test_default_keeps_text_untouched() -> None:
    """默认关：不得改动文本值（既有产出不变）。"""
    value, _unit = normalize_value(OCR_CJK_TEXT, _spec())

    assert value == OCR_CJK_TEXT


def test_option_removes_whitespace_between_cjk_characters() -> None:
    """开启后：汉字之间的 OCR 空格被删掉，值等于真值。"""
    value, _unit = normalize_value(OCR_CJK_TEXT, _spec(collapse_whitespace=True))

    assert value == "示例服务合同"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # 英文之间的空白必须保留（否则 "Sample Service Contract" 会被粘成一串）
        ("Sample Service Contract", "Sample Service Contract"),
        # 连续空白折叠成单个；数字前的空格有意义，保留
        ("总额  1234 元", "总额 1234 元"),
        # 汉字与英文字母相邻时，保留它们之间的空白（只删"汉字紧邻汉字"）
        ("示例 Service 合同", "示例 Service 合同"),
        # 首尾空白仍按原规则去掉
        ("  示例服务合同  ", "示例服务合同"),
    ],
)
def test_option_only_targets_cjk_to_cjk_spacing(raw: str, expected: str) -> None:
    """不是"一删了之"：只清汉字之间的噪声，其它空白照旧。"""
    value, _unit = normalize_value(raw, _spec(collapse_whitespace=True))

    assert value == expected


@pytest.mark.parametrize("field_type", ["amount", "date", "integer", "number"])
def test_option_on_non_text_type_is_rejected(field_type: str) -> None:
    """配在非文本类字段上必须报错 —— 那种组合会静默失效。"""
    with pytest.raises(ValueError, match="collapse_whitespace"):
        FieldSpec.from_dict(
            {"name": "party", "label": "当事方", "type": field_type, "collapse_whitespace": True}
        )


def test_unknown_field_keys_are_still_rejected() -> None:
    """新增选项不能放松既有的未知键校验。"""
    with pytest.raises(ValueError, match="未知配置项"):
        FieldSpec.from_dict({"name": "party", "label": "当事方", "nope": 1})
