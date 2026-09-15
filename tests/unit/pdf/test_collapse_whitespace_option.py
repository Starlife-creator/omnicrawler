"""OCR 空白归一：**按来源默认生效**，显式声明可覆盖（§5.8 #24，2026-09-15 拍板）。

背景（实测）：`chi_sim` 会在汉字之间插空格（"示例服务合同" → "示例  服务  合同"），
而取值模式 `(?P<value>[^\\n]+)` 会把整行余下内容连同空格一起收进来 ⇒ 空格留在值里，
**且实测置信度 0.98、会被自动放行** ⇒ 交付脏值（PDF 侧专项基准把它判为
「错值自动放行」）。旧做法是逐字段 `collapse_whitespace: true`（默认关）——等于把
"记得去开"交给用户。现改为：

* **未声明**（`None`）：值取自 **OCR 页** ⇒ 折叠；原生文字层 ⇒ 原样（文档空白有意义）；
* **显式 `True`/`False`**：始终优先（`False` 仍可关掉，供确实要留原始空白的场景）。

本文件锁：
1. 按来源的四种组合（未声明×原生 / 未声明×OCR / 显式 True×原生 / 显式 False×OCR）；
2. 只删"汉字紧邻汉字"那一个空格（英文/数字之间的空白必须保留）；
3. 配在数值/日期类字段上必须**报错**（不允许静默失效，沿用 D26/D27 原则）。
"""

from __future__ import annotations

import pytest

from omnicrawler.pdfx.config import FieldSpec
from omnicrawler.pdfx.normalization import normalize_value

#: OCR 会在汉字之间插空格 —— 这是本规则要解决的噪声
OCR_CJK_TEXT = "示例  服务  合同"
#: 归一后的真值
CLEAN = "示例服务合同"


def _spec(**overrides) -> FieldSpec:
    return FieldSpec.from_dict({"name": "party", "label": "当事方", "type": "text", **overrides})


def test_undeclared_on_native_text_layer_keeps_text_untouched() -> None:
    """未声明 + **原生文字层**：原样保留（文档本身的空白有意义，不得改动既有产出）。"""
    value, _unit = normalize_value(OCR_CJK_TEXT, _spec(), from_ocr=False)

    assert value == OCR_CJK_TEXT


def test_undeclared_on_ocr_page_collapses() -> None:
    """未声明 + **OCR 页**：默认折叠 —— 这是本轮的行为变更（新默认生效）。"""
    value, _unit = normalize_value(OCR_CJK_TEXT, _spec(), from_ocr=True)

    assert value == CLEAN


def test_explicit_true_overrides_native_source() -> None:
    """显式 True 优先：即使来自原生文字层也折叠（声明权高于来源推断）。"""
    value, _unit = normalize_value(OCR_CJK_TEXT, _spec(collapse_whitespace=True), from_ocr=False)

    assert value == CLEAN


def test_explicit_false_still_opts_out_on_ocr_page() -> None:
    """显式 False 仍可关掉：OCR 页上也保留原样（给"确实要留原始空白"的场景）。"""
    value, _unit = normalize_value(OCR_CJK_TEXT, _spec(collapse_whitespace=False), from_ocr=True)

    assert value == OCR_CJK_TEXT


def test_undeclared_default_is_none_not_false() -> None:
    """配置层：未声明必须是 `None`（＝按来源），**不能退化成 `False`**（否则新默认失效）。"""
    assert _spec().collapse_whitespace is None
    assert _spec(collapse_whitespace=False).collapse_whitespace is False
    assert _spec(collapse_whitespace=True).collapse_whitespace is True


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
def test_only_cjk_to_cjk_spacing_is_removed(raw: str, expected: str) -> None:
    """不是"一删了之"：只清汉字之间的噪声，其它空白照旧（OCR 来源下生效）。"""
    value, _unit = normalize_value(raw, _spec(), from_ocr=True)

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
