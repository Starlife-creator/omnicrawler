"""Phase 3e 归一化修复测试（D49 会计负数 / D50 外币 / D51 Decimal / D52 日期回退 / D53 enum 否定）。"""

from __future__ import annotations

from omnicrawler.pdfx.config import FieldSpec
from omnicrawler.pdfx.normalization import normalize_amount, normalize_date, normalize_value


def test_d49_accounting_bracket_negative() -> None:
    assert normalize_amount("(1,234)", "元") == ("-1234", "元")
    assert normalize_amount("（1,234）万元", "万元") == ("-1234", "万元")


def test_d49_plain_negative() -> None:
    assert normalize_amount("-3,000", "元") == ("-3000", "元")


def test_d50_foreign_currency_rejected() -> None:
    # 3,000万美元 不能当 3000 万元人民币
    assert normalize_amount("3,000万美元", "万元") == (None, "万元")
    assert normalize_amount("100港币", "元") == (None, "元")


def test_s251_foreign_currency_symbol_forms_rejected() -> None:
    # 符号/ISO 形式外币不再静默按人民币标准化
    assert normalize_amount("$100", "元") == (None, "元")
    assert normalize_amount("USD 100", "元") == (None, "元")
    assert normalize_amount("€50", "元") == (None, "元")
    assert normalize_amount("HK$3,000", "元") == (None, "元")
    assert normalize_amount("200 USD", "元") == (None, "元")
    # ¥ 是人民币符号，不应拒绝
    assert normalize_amount("¥1,200", "元") == ("1200", "元")


def test_s251_large_unit_before_wan() -> None:
    # “千万元”/“百万”/“百元” 等大单位不再被 “万元/元” 抢先命中
    assert normalize_amount("3千万元", "元") == ("30000000", "元")
    assert normalize_amount("3千万元", "万元") == ("3000", "万元")
    assert normalize_amount("2.5百万元", "元") == ("2500000", "元")
    assert normalize_amount("3百万", "元") == ("3000000", "元")
    assert normalize_amount("8百元", "元") == ("800", "元")


def test_d51_decimal_no_ieee_artifacts() -> None:
    assert normalize_amount("1.15亿元", "元") == ("115000000", "元")
    assert normalize_amount("1.15亿", "万元") == ("11500", "万元")


def test_d52_date_invalid_pattern_falls_through() -> None:
    # “2023年13月” 该 pattern 构造失败，应继续尝试下一 pattern（年份级）
    year, _ = normalize_date("2023年13月")
    assert year == "2023"
    assert normalize_date("2024年5月20日") == ("2024-05-20", None)


def test_d53_enum_negation_not_matched() -> None:
    spec = FieldSpec(
        name="rel", label="关系", type="enum",
        value_aliases={
            "全资子公司": ["全资子公司"],
            "控股子公司": ["控股子公司"],
        },
    )
    # 含否定词不应归一为正向关系
    value, _ = normalize_value("非全资子公司", spec)
    assert value == "非全资子公司"
    # 正向精确/别名仍归一
    value2, _ = normalize_value("控股子公司", spec)
    assert value2 == "控股子公司"


def test_d53_enum_longest_alias_wins() -> None:
    spec = FieldSpec(
        name="rel", label="关系", type="enum",
        value_aliases={
            "控股子公司": ["子公司"],
            "孙公司": ["控股子公司的子公司"],
        },
    )
    value, _ = normalize_value("控股子公司的子公司", spec)
    assert value == "孙公司"
