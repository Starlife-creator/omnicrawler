"""数值 token 的**结构性判定**：多分隔符不许静默截断（2026-09-13）。

## 背景：一条真实缺陷

账本此前把「OCR 把千分位逗号读成小数点」记为"识别错误，只能靠复核环节兜住"。
**这个说法不完整**：旧 `_number()` 用 ``[-+]?\\d[\\d,，]*(?:\\.\\d+)?`` 抓数字，
遇到 OCR 输出的 ``12.345.67`` **只会匹配到 ``12.345``** —— 于是交付出去的是一个
**看起来完全合理、但错误的数**，且不留痕迹。同一函数还会把
``1.234.567`` 截成 ``1.234``、把欧式 ``1.234,56`` 截成 ``1.234``。

这违反本项目"异常不得伪装成功／产出不许静默失效"的既有原则
（与 `pdfx/config.py` 的 D26/D27 同源），因此改为**先判结构**：

* 分隔符 ≤ 1 个：沿用常规读法（``1,234`` → 1234；``1.234`` → 1.234）；
* 分隔符 ≥ 2 个：只接受两种**可判定**的读法 ——
  ① 全部当千分位（分组规范）；② 最右为小数点、其余为千分位（整数部分分组规范）；
  两者都成立取 ①；**都不成立 → None**（交人工复核，不猜）。

原始文本仍留在 `…_原始值`／原文证据里，所以"恢复"本身也可审计。
"""

from __future__ import annotations

import pytest

from omnicrawler.pdfx.config import FieldSpec
from omnicrawler.pdfx.normalization import _number, normalize_amount, normalize_value

#: OCR 常见形态（含全角/中文标点）→ 期望归一结果
RECOVERABLE: list[tuple[str, str]] = [
    ("12,345.67 元", "12345.67"),          # 正常的美式写法（回归基准）
    ("12.345.67 元", "12345.67"),          # OCR 把千分位逗号读成小数点 → 恢复
    ("12．345．67 元", "12345.67"),           # 同上，全角句点
    ("12。345。67 元", "12345.67"),           # 同上，中文句号
    ("1.234.567 元", "1234567"),           # 全部为千分位（分组规范）
    ("1,234,567 元", "1234567"),
    ("1,234,567.89 元", "1234567.89"),
    ("1.234,56 元", "1234.56"),            # 欧式：点千分位、逗号小数
    ("12.345.678.9 元", "12345678.9"),     # 全部千分位不成立（末段 1 位），最右当小数点则成立
]

#: 结构上**无法判定**的串 → 必须返回 None（交复核），绝不许猜一个数出来
UNJUDGEABLE: list[str] = [
    "1.2.3.4 元",        # 两种读法的分组都不规范
    "2023.12.31",        # 三段式日期形态进入数值字段（首段 4 位）
    "1..2 元",           # 空段
]


def test_ocr_thousands_separator_is_recovered() -> None:
    """核心修复：``12.345.67`` 必须恢复成 ``12345.67``，而不是旧的 ``12.345``。"""
    assert normalize_amount("12.345.67 元", "元") == ("12345.67", "元")


@pytest.mark.parametrize(("raw", "expected"), RECOVERABLE)
def test_recoverable_forms(raw: str, expected: str) -> None:
    """可判定形态一律给对：美式、欧式、全角标点、纯千分位分组。"""
    assert normalize_amount(raw, "元")[0] == expected


@pytest.mark.parametrize("raw", UNJUDGEABLE)
def test_unjugdeable_returns_none_instead_of_guessing(raw: str) -> None:
    """判不了就交复核 —— 这是本修复的**主要**行为变化，不是"更聪明地猜"。"""
    assert normalize_amount(raw, "元")[0] is None
    assert _number(raw) is None


def test_never_returns_a_truncated_prefix() -> None:
    """**防回归契约**：结果要么是 None，要么是完整值；永不是被截断的前缀。

    这是本次缺陷的本质 —— 旧实现返回 `12.345`（`12.345.67` 的前缀）、
    `1.234`（`1.234.567` 的前缀），两者都"看起来合理"因而无人察觉。
    """
    for raw in ("12.345.67 元", "1.234.567 元", "1.234,56 元"):
        value = normalize_amount(raw, "元")[0]
        digits_only = raw.replace(",", "").replace(".", "").split()[0]
        if value is not None:
            # 恢复出的数值，其"去掉分隔符后的数字串"必须等于原串去掉分隔符后的数字串
            assert value.replace(".", "") in digits_only, (
                f"{raw} → {value}：不是完整值（疑似截断）"
            )


def test_single_separator_readings_unchanged() -> None:
    """单分隔符沿用旧读法（避免把 `1.234` 误判成千分位）。"""
    assert normalize_amount("1,234 元", "元")[0] == "1234"
    assert normalize_amount("1.234 元", "元")[0] == "1.234"
    assert normalize_amount("0.5 元", "元")[0] == "0.5"


def test_accounting_bracket_and_sign_still_work() -> None:
    """D49 会计负数括号与本修复并存（括号内的串同样走结构判定）。"""
    assert normalize_amount("(1,234)", "元") == ("-1234", "元")
    assert normalize_amount("（12,345.67）元", "元") == ("-12345.67", "元")
    assert normalize_amount("-3,000 元", "元") == ("-3000", "元")


def test_number_and_integer_field_types_share_the_rule() -> None:
    """`number` / `integer` 字段类型复用同一判定（不再各自静默截断）。"""
    number_spec = FieldSpec(name="n", label="数量", type="number")
    integer_spec = FieldSpec(name="i", label="整数", type="integer")

    assert normalize_value("12.345.67", number_spec)[0] == "12345.67"
    assert normalize_value("12.345.67", integer_spec)[0] == "12345"
    assert normalize_value("1.2.3.4", number_spec)[0] is None


def test_percent_shares_the_rule() -> None:
    """百分比同理（旧实现会把 `12.345.67%` 截成 `12.3`）。"""
    from omnicrawler.pdfx.normalization import normalize_percent

    assert normalize_percent("12.345.67%")[0] == "12345.67"
    assert normalize_percent("1.2.3.4%")[0] is None
