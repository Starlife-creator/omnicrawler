"""金额归一：**宿主侧**的基础能力（从 `pdfx.normalization` 上提）。

为什么要上提
------------
`pdfx` 正被改造成**可选组件**（条目 7）。但金额归一不是"文档专属"能力——
质量基准（`services/pdf_quality_benchmark.py`）与通用后处理都要用它，
而它此前住在 `pdfx` 里，导致宿主反向依赖文档子系统：

    services/pdf_quality_benchmark.py:47
        from ..pdfx.normalization import normalize_amount   ← 模块顶层导入

后果：`pdfx` 组件未安装时，连质量基准模块都 import 不进来——
宿主启动阶段就可能崩，这与"可选组件"直接冲突。

**搬迁的是纯函数**：这些逻辑只做字符串/数字处理，不碰 PDF、不碰配置、不碰数据库，
放在宿主侧（`quality/`）语义上也更准确。

数值读法的关键约定（原样保留，见 `_parse_numeric_token` 的注释）
------------------------------------------------------------------
OCR 造成的千分位误读（`12.345.67` 实为 `12,345.67`）**绝不静默截断**：
可判定时恢复，不可判定时返回 `None` 交人工复核。这是本项目
"异常不得伪装成功、产出不许静默失效"原则的直接体现，搬迁时一字未改。
"""
from __future__ import annotations

import re
from decimal import Decimal

#: 金额单位 → 相对「元」的倍数（顺序影响匹配：长单位优先，如「亿元」先于「亿」）
AMOUNT_UNITS: dict[str, int] = {
    "亿元": 100_000_000,
    "亿": 100_000_000,
    "千万元": 10_000_000,
    "百万元": 1_000_000,
    "百万": 1_000_000,
    "万元": 10_000,
    "万": 10_000,
    "千元": 1_000,
    "百元": 100,
    "元": 1,
}

#: 外币识别补符号/ISO 形式（$100 / USD 100 / €50 / HK$）；¥ 是人民币符号不拒。
_FOREIGN_CURRENCY_RE = re.compile(
    r"(?:美元|美金|港币|港元|欧元|日元|日圆|英镑|瑞郎|法郎)"
    r"|(?<![A-Za-z])(?:USD|EUR|JPY|GBP|HKD|AUD|CAD)(?=\s*\d)"
    r"|(?<=[\d，,.])\s*(?:USD|EUR|JPY|GBP|HKD|AUD|CAD)\b"
    r"|(?:HK\$|\$|€|£)(?=\s*\d)"
)

#: 分隔符归一映射：全角/中文标点先收敛到 ASCII——`．`/`。` 都是 OCR 的常见形态。
_SEPARATOR_TRANSLATION = str.maketrans({"，": ",", "．": ".", "。": ".", "｡": "."})


def _grouping_valid(token: str) -> bool:
    """千分位分组是否规范：首段 1–3 位数字，其后每段**恰好** 3 位。"""
    parts = re.split(r"[,.]", token)
    if not parts or not all(part.isdigit() for part in parts):
        return False
    if len(parts) == 1:
        return bool(parts[0])
    return 1 <= len(parts[0]) <= 3 and all(len(part) == 3 for part in parts[1:])


def parse_numeric_token(token: str) -> float | None:
    """把数字串转成 float；**多个分隔符时做结构性判定，绝不静默截断**。

    * 分隔符 ≤ 1 个：常规读法（`1,234` → 1234；`1.234` → 1.234）；
    * 分隔符 ≥ 2 个：只接受两种**可判定**的读法——
      ① 全部当千分位（`1.234.567` → 1234567）；
      ② 最右为小数点、其余为千分位（`12.345.67` → 12345.67；欧式 `1.234,56` → 1234.56）；
      两种都成立取 ①；**都不成立返回 None**，交人工复核而不是交付猜出来的数。
    """
    normalized = token.translate(_SEPARATOR_TRANSLATION)
    if sum(character in ",." for character in normalized) <= 1:
        try:
            return float(normalized.replace(",", ""))
        except ValueError:
            return None
    if _grouping_valid(normalized):
        try:
            return float(normalized.replace(",", "").replace(".", ""))
        except ValueError:
            return None
    cut = max(normalized.rfind(","), normalized.rfind("."))
    integer_part, fraction = normalized[:cut], normalized[cut + 1:]
    if integer_part and fraction.isdigit() and _grouping_valid(integer_part):
        digits = integer_part.replace(",", "").replace(".", "")
        try:
            return float(f"{digits}.{fraction}")
        except ValueError:
            return None
    return None


def extract_number(text: str) -> float | None:
    """提取数字；识别会计负数括号 `(1,234)` / `（1,234）`。"""
    stripped = text.strip()
    bracket = re.search(r"[(（]\s*([\d,，.．。]+)\s*[)）]", stripped)
    if bracket:
        value = parse_numeric_token(bracket.group(1))
        return None if value is None else -value
    match = re.search(r"[-+]?\d[\d,，.．。]*", text)
    if not match:
        return None
    token = match.group(0).rstrip(",，．。")
    sign = -1.0 if token.startswith("-") else 1.0
    value = parse_numeric_token(token.lstrip("+-"))
    return None if value is None else sign * value


def format_decimal(value: object) -> str:
    """Decimal 换算格式化，避免 IEEE754 浮点误差（1.15亿 → 114999999.99999999）。"""
    decimal_value = Decimal(str(value))
    if decimal_value == decimal_value.to_integral_value():
        return str(decimal_value.to_integral_value())
    return format(decimal_value, "f").rstrip("0").rstrip(".")


def normalize_amount(raw: str, target_unit: str | None = "元") -> tuple[str | None, str | None]:
    """把金额文本归一为指定单位的数值字符串。

    外币（亿美元/港币/$100/USD 100/€50）无法按人民币换算 ⇒ 返回 ``(None, unit)``
    交人工复核，而不是给一个错误的换算值。
    """
    value = extract_number(raw)
    if value is None:
        return None, target_unit
    if _FOREIGN_CURRENCY_RE.search(raw):
        return None, target_unit
    source_unit = next((unit for unit in AMOUNT_UNITS if unit in raw), None)
    source_multiplier = AMOUNT_UNITS.get(source_unit or "元", 1)
    target = target_unit or "元"
    target_multiplier = AMOUNT_UNITS.get(target, 1)
    yuan = Decimal(str(value)) * Decimal(source_multiplier)
    normalized = yuan / Decimal(target_multiplier)
    return format_decimal(normalized), target
