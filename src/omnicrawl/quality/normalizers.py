"""值级清洗：列类型推断 + 无损规范化（AutoDataCleaner 借鉴）。

设计原则（非三层漏斗教条，而是统一安全准则）：
    1. 只在「可证明无损」时变换：任何转换要么可逆、要么能验证不丢信息；
       推断失败 / 混合类型 / 哨兵字符串 → 原样保留，绝不猜测。
    2. 幂等：对已清洗数据再跑一遍零变化（支撑 reprocess 与重复运行）。
    3. 留证据、可回滚：每个被改的单元格写入 evidence["_normalization"]，
       记录 original 与 rule。
    4. 单一策略对象：NormalizePolicy 控制一切（L1 幂等 / L2 规则 / L3 槽位默认关）。

层级（与 B-2 漏斗同构但语义独立）：
    L1 幂等规范化：trim、纯 int/float 无损转换、全角数字归位——列内 uniform 才做类型强转
    L2 规则修复：百分比/金额/日期/URL 统一——按单元格安全可验证地格式归一
    L3 受限 LLM（槽位）：默认关闭，本模块不实现 LLM 集成，策略字段预留

本模块不改共享状态、不抛异常到上游（调用方 try/except 兜底），
与 pdfx 的「按 spec 归一化」差异化：这里面向「网页脏数据列类型推断」。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from ..core.safe_data import safe_regex_search
from .dupe_filter import strip_tracking_params

# 全角数字/正负号 → 半角（网页常见全角数字）
_FULLWIDTH_TRANS = str.maketrans("０１２３４５６７８９＋－．", "0123456789+-.")

TypeKind = Literal["text", "integer", "float", "percent", "money", "date", "url"]

_TYPE_KINDS: tuple[TypeKind, ...] = ("integer", "float", "percent", "money", "date", "url", "text")

_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$")
_PCT_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?)\s*([%％])$")
_DATE_YMD_RE = re.compile(r"^(\d{4})[年/\-.](\d{1,2})[月/\-.](\d{1,2})日?$")
_DATE_DMY_RE = re.compile(r"^(\d{1,2})[年/\-.](\d{1,2})[月/\-.](\d{2,4})$")
_MONEY_RE = re.compile(
    r"^(?:(¥|￥|\$|USD|RMB)\s*)?([\d,]+(?:\.\d{1,4})?)\s*(万元|亿元|万|亿|元|块)?$"
)
_URL_RE = re.compile(r"^(?:https?://|www\.)[^\s]+$", re.IGNORECASE)

# 统一列强转所需的最低可解析比例（< 该值视为混合列，L1 类型强转停用）
_UNIFORM_THRESHOLD = 0.98
# 单条值参与推断/转换的最大长度（防御病态长串）
_MAX_CELL_LENGTH = 4096
# 列推断抽样上限（大列只抽前 N 条非空值，控制开销）
_SAMPLE_LIMIT = 500

_MONEY_MULTIPLIER = {"万元": 10_000, "万": 10_000, "亿元": 100_000_000, "亿": 100_000_000, "元": 1, "块": 1}


# ── 策略与结果数据结构 ──────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class NormalizePolicy:
    """值清洗策略。L1/L2 默认开启，L3 槽位默认关闭。"""

    l1_enabled: bool = True
    l2_enabled: bool = True
    l3_enabled: bool = False  # 设计预留：本模块不做 LLM 集成（见模块 docstring），受限 LLM 修复走 quality/shadow_repair
    money_unit: str = "元"
    date_format: str = "iso"
    strip_tracking: bool = True
    max_cell_length: int = _MAX_CELL_LENGTH
    # 显式列类型覆盖：{field_name: TypeKind}（配置 quality.normalize.types）
    types: dict[str, TypeKind] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ColumnProfile:
    """单列类型画像（由非空值推断）。"""

    name: str
    kind: TypeKind
    confidence: float  # 可解析比例 0~1
    uniform: bool  # confidence >= _UNIFORM_THRESHOLD（决定 L1 类型强转是否允许）
    rule: str = ""  # 推断依据（人类可读）


@dataclass(slots=True)
class NormalizeCell:
    """单单元格规范化结果（含证据）。"""

    value: Any
    changed: bool = False
    original: Any = None
    rule: str = ""
    skipped: bool = False  # 应转换但被安全闸拦下（记录未改）


@dataclass(frozen=True, slots=True)
class FieldNormalizeStats:
    name: str
    kind: TypeKind
    uniform: bool
    confidence: float
    changed_cells: int
    rules: dict[str, int]
    skipped: int


@dataclass(frozen=True, slots=True)
class NormalizeReport:
    enabled_l1: bool
    enabled_l2: bool
    total_changed: int
    fields: tuple[FieldNormalizeStats, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled_l1": self.enabled_l1,
            "enabled_l2": self.enabled_l2,
            "total_changed": self.total_changed,
            "fields": [
                {
                    "name": f.name, "kind": f.kind, "uniform": f.uniform,
                    "confidence": round(f.confidence, 4), "changed_cells": f.changed_cells,
                    "rules": dict(f.rules), "skipped": f.skipped,
                }
                for f in self.fields
            ],
        }


# ── 解析器（各自返回规范化字符串；无法安全解析返回 None）────────────────


def _normalize_ws(value: str) -> str:
    """全角数字归位 + 去首尾空白；L1 文本层只做这两件无损操作。"""
    return value.translate(_FULLWIDTH_TRANS).strip()


def _canonical_decimal(number: Decimal) -> str:
    """Decimal 归一为字符串：仅在小数点后去除尾随 0，整数位永不改动。"""
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _try_integer(value: str) -> str | None:
    """纯整数规范化：str(int(v)) 必须与原始数字串完全一致（保证无损）。

    前导零（如 "00123"）会被拦下——前导零常有语义（编码/序号），不猜。
    返回规范化字符串（与原始值相同也返回，由调用方比较判定是否变更）。
    """
    text = _normalize_ws(value)
    if not _INT_RE.match(text):
        return None
    if len(text) > 1 and text.lstrip("+-").startswith("0"):
        return None  # 前导零：可能有编码语义，交给 review
    return str(int(text))


def _try_float(value: str) -> str | None:
    """浮点规范化：Decimal 归一，去掉小数尾随无意义的 0（"1.50"→"1.5"）。

    不接受千分位逗号（与小数逗号歧义），混合/歧义列不转换。
    """
    text = _normalize_ws(value)
    if not _FLOAT_RE.match(text):
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if not number.is_finite():
        return None
    return _canonical_decimal(number)


def _try_percent(value: str) -> str | None:
    """百分比统一：全角 ％→半角 %，去掉符号与数字间空白。

    不做 ÷100 换算（含义取决于下游消费者，属于语义猜测）。
    """
    text = _normalize_ws(value)
    match = _PCT_RE.match(text)
    if not match:
        return None
    return f"{match.group(1)}%"


def _try_money(value: str, default_unit: str) -> tuple[str, str] | None:
    """金额规范化：剥离货币符号与千分位，中单位（万/亿）换算到默认单位。

    Returns:
        (规范化数值字符串, 规则说明)；无法安全解析返回 None。
    """
    text = _normalize_ws(value)
    match = _MONEY_RE.match(text)
    if not match:
        return None
    symbol, digits, unit = match.groups()
    # 千分位校验：有逗号必须按三位分组（"1,299" 合法，"12,99" 歧义拒绝）
    if "," in digits:
        int_part = digits.split(".")[0]
        if not re.fullmatch(r"\d{1,3}(?:,\d{3})+", int_part):
            return None
        digits = digits.replace(",", "")
    if not _FLOAT_RE.match(digits):
        return None
    try:
        amount = Decimal(digits)
    except InvalidOperation:
        return None
    multiplier = _MONEY_MULTIPLIER.get(unit or "", 1)
    amount = amount * multiplier
    if not amount.is_finite():
        return None
    canonical = _canonical_decimal(amount)
    parts: list[str] = []
    if symbol:
        parts.append(f"货币符号 {symbol!r}")
    if unit:
        parts.append(f"单位 {unit}→{default_unit}")
    rule = "金额规范化（" + "；".join(parts) + "）" if parts else "金额规范化"
    return canonical, rule


def _try_date(value: str) -> tuple[str, str] | None:
    """日期统一为 ISO YYYY-MM-DD。

    歧义防御：月/日 顺序无法判定（两段都 ≤12）→ 拒绝转换（不猜）。
    """
    text = _normalize_ws(value)
    match = _DATE_YMD_RE.match(text)
    if match:
        year, month, day = (int(part) for part in match.groups())
        canonical = f"{year:04d}-{month:02d}-{day:02d}"
        return canonical, "日期统一 ISO"
    match = _DATE_DMY_RE.match(text)
    if match:
        first, second, year = (int(part) for part in match.groups())
        if year < 100:
            year += 2000
        if not 1 <= year <= 9999 or not 1 <= first <= 31 or not 1 <= second <= 31:
            return None
        if first <= 12 and second <= 12:
            return None  # 月/日歧义（M/D 与 D/M 无法区分）→ 不猜
        if first > 12:
            day, month = first, second  # 日/月/年
        else:
            day, month = second, first  # 月/日/年
        canonical = f"{year:04d}-{month:02d}-{day:02d}"
        return canonical, "日期统一 ISO"
    return None


def _try_url(value: str, *, strip_tracking: bool) -> tuple[str, str] | None:
    """URL 规范化：剥离 tracking 参数（复用 dupe_filter 的清单）。"""
    text = _normalize_ws(value)
    if not _URL_RE.match(text):
        return None
    if not strip_tracking:
        return None
    stripped = strip_tracking_params(text)
    if stripped == text:
        return None
    return stripped, "URL 去 tracking 参数"


def _parse_by_kind(kind: TypeKind, value: str, policy: NormalizePolicy) -> tuple[str, str] | None:
    """按列类型对单个字符串尝试规范化，返回 (canonical, rule)；无法安全解析返回 None。"""
    if kind == "integer":
        canonical = _try_integer(value)
        return (canonical, "整数规范化") if canonical is not None else None
    if kind == "float":
        canonical = _try_float(value)
        return (canonical, "浮点规范化") if canonical is not None else None
    if kind == "percent":
        canonical = _try_percent(value)
        return (canonical, "百分比统一半角") if canonical is not None else None
    if kind == "money":
        return _try_money(value, policy.money_unit)
    if kind == "date":
        return _try_date(value)
    if kind == "url":
        return _try_url(value, strip_tracking=policy.strip_tracking)
    return None


# ── 列类型推断 ───────────────────────────────────────────────


def _parseable(kind: TypeKind, value: str) -> bool:
    """仅判断可解析性（不含规则文案），供推断计数使用。"""
    probe = NormalizePolicy()
    if kind == "money":
        return _try_money(value, probe.money_unit) is not None
    if kind == "date":
        return _try_date(value) is not None
    if kind == "url":
        return _try_url(value, strip_tracking=False) is not None or _URL_RE.match(_normalize_ws(value)) is not None
    if kind in ("integer", "float", "percent"):
        return _parse_by_kind(kind, value, probe) is not None
    return True


def infer_column_type(values: list[Any], *, name: str = "") -> ColumnProfile:
    """从非空字符串值推断列类型。

    规则：统计各类型可解析比例，取最高者；整数严格优先于浮点
    （整数可解析必然浮点可解析，需按语义优先 int）。比例 ≥ 阈值视为 uniform。
    """
    candidates = [
        value for value in values
        if isinstance(value, str) and value.strip() and len(value) <= _MAX_CELL_LENGTH
    ][:_SAMPLE_LIMIT]
    if not candidates:
        return ColumnProfile(name=name, kind="text", confidence=1.0, uniform=True, rule="空列视为 text")
    total = len(candidates)
    best_kind: TypeKind = "text"
    best_rate = 0.0
    for kind in ("integer", "float", "percent", "money", "date", "url"):
        rate = sum(1 for value in candidates if _parseable(kind, value)) / total
        if rate > best_rate:
            best_kind, best_rate = kind, rate
    # 全部解析失败 → text（仍是可写文本，不猜类型）
    if best_rate == 0.0:
        best_kind, best_rate = "text", 1.0
    return ColumnProfile(
        name=name,
        kind=best_kind,
        confidence=best_rate,
        uniform=best_rate >= _UNIFORM_THRESHOLD,
        rule=f"{best_kind} 可解析比例 {best_rate:.0%}",
    )


# ── 单值规范化 ───────────────────────────────────────────────


def normalize_cell(
    raw: Any,
    kind: TypeKind,
    policy: NormalizePolicy,
    *,
    require_uniform: bool,
) -> NormalizeCell:
    """按列类型规范化单个单元格。

    Args:
        require_uniform: True 表示仅当列 uniform 时才做 L1 类型强转（数值/整数）；
            L2 规则（percent/money/date/url）按单元格安全归一，不受列一致性限制。
    """
    if raw is None or not isinstance(raw, str):
        return NormalizeCell(value=raw)
    if not raw.strip() or len(raw) > policy.max_cell_length:
        return NormalizeCell(value=raw)

    # L1：文本 trim（无损，恒允许）
    if kind == "text" and policy.l1_enabled:
        trimmed = _normalize_ws(raw)
        if trimmed != raw:
            return NormalizeCell(value=trimmed, changed=True, original=raw, rule="去除首尾空白")
        return NormalizeCell(value=raw)

    # L1：数值/整数类型强转（须列 uniform，且解析无损）
    if kind in ("integer", "float") and policy.l1_enabled:
        if require_uniform:
            parsed = _parse_by_kind(kind, raw, policy)
            if parsed is None:
                return NormalizeCell(value=raw, skipped=True, original=raw)
            canonical, rule = parsed
            if canonical == raw:
                return NormalizeCell(value=raw)  # 已是规范形式，无需改动
            return NormalizeCell(value=canonical, changed=True, original=raw, rule=rule)
        return NormalizeCell(value=raw)

    # L2：格式统一规则（percent/money/date/url）——单元格级安全，不要求列 uniform
    if kind in ("percent", "money", "date", "url") and policy.l2_enabled:
        parsed = _parse_by_kind(kind, raw, policy)
        if parsed is None:
            return NormalizeCell(value=raw, skipped=True, original=raw)
        canonical, rule = parsed
        if canonical == raw:
            return NormalizeCell(value=raw)  # 已是规范形式，无需改动
        return NormalizeCell(value=canonical, changed=True, original=raw, rule=rule)

    return NormalizeCell(value=raw)


# ── 记录级入口 ───────────────────────────────────────────────


def normalize_records(
    records: list[Any],
    *,
    fields: dict[str, Any] | None = None,
    policy: NormalizePolicy | None = None,
) -> NormalizeReport:
    """对一组 ExtractedRecord 做列级值清洗（原地修改 data 与 evidence）。

    Args:
        records: ExtractedRecord 列表（每条需有 .data 与 .evidence 映射）。
        fields: extract.fields 规则映射（取其键作为清洗范围）；None 时清洗
            data 中全部字符串键。
        policy: 清洗策略；None 用默认（L1+L2 on，L3 off）。

    Returns:
        NormalizeReport（含各列画像与变更统计；上游可据此日志/监控）。
    """
    policy = policy or NormalizePolicy()
    if not records:
        return NormalizeReport(policy.l1_enabled, policy.l2_enabled, 0, ())

    # 清洗范围：显式 types ∪ extract.fields 键 ∪ （无 fields 时全部 data 键）
    scope: set[str] = set(policy.types)
    if fields:
        scope.update(str(key) for key in fields)
    else:
        for record in records:
            scope.update(str(key) for key in (getattr(record, "data", None) or {}))
    if not scope:
        return NormalizeReport(policy.l1_enabled, policy.l2_enabled, 0, ())

    stats: list[FieldNormalizeStats] = []
    total_changed = 0
    for field_name in sorted(scope):
        values = [
            record.data.get(field_name) for record in records
            if isinstance(getattr(record, "data", None), dict)
        ]
        explicit = policy.types.get(field_name)
        if explicit in _TYPE_KINDS:
            profile = ColumnProfile(
                name=field_name, kind=explicit, confidence=1.0, uniform=True,
                rule="显式配置类型",
            )
        else:
            profile = infer_column_type(values, name=field_name)
        rules: Counter[str] = Counter()
        changed_cells = 0
        skipped = 0
        for record in records:
            data = getattr(record, "data", None)
            if not isinstance(data, dict) or field_name not in data:
                continue
            cell = normalize_cell(
                data[field_name], profile.kind, policy,
                require_uniform=profile.uniform,
            )
            if cell.changed:
                data[field_name] = cell.value
                changed_cells += 1
                rules[cell.rule] += 1
                # 证据链：原始值 + 规则，可回滚/复核
                evidence = getattr(record, "evidence", None)
                if isinstance(evidence, dict):
                    normalized_evidence = evidence.setdefault("_normalization", {})
                    if isinstance(normalized_evidence, dict):
                        normalized_evidence[field_name] = {
                            "original": cell.original, "rule": cell.rule,
                        }
            elif cell.skipped:
                skipped += 1
        if changed_cells or skipped or profile.kind != "text":
            stats.append(FieldNormalizeStats(
                name=field_name,
                kind=profile.kind,
                uniform=profile.uniform,
                confidence=profile.confidence,
                changed_cells=changed_cells,
                rules=dict(rules),
                skipped=skipped,
            ))
        total_changed += changed_cells
    return NormalizeReport(
        enabled_l1=policy.l1_enabled,
        enabled_l2=policy.l2_enabled,
        total_changed=total_changed,
        fields=tuple(stats),
    )


def policy_from_config(cfg: dict[str, Any] | None) -> NormalizePolicy:
    """从 quality.normalize 配置段构造策略（缺省字段用默认值）。"""
    cfg = cfg if isinstance(cfg, dict) else {}
    types = cfg.get("types", {})
    return NormalizePolicy(
        l1_enabled=bool(cfg.get("l1_enabled", True)),
        l2_enabled=bool(cfg.get("l2_enabled", True)),
        l3_enabled=bool(cfg.get("l3_enabled", False)),
        money_unit=str(cfg.get("money_unit", "元")) or "元",
        date_format=str(cfg.get("date_format", "iso")) or "iso",
        strip_tracking=bool(cfg.get("strip_tracking", True)),
        max_cell_length=int(cfg.get("max_cell_length", _MAX_CELL_LENGTH)),
        types={
            str(key): kind
            for key, value in types.items()
            if (kind := str(value)) in _TYPE_KINDS
        } if isinstance(types, dict) else {},
    )


# ── 公开值级函数（H4：AST 求值器 / transform 表达式可用）────────────────
# 统一契约：输入非字符串原样返回；解析失败返回原值（无损，不猜测）；
# 任何异常不外抛（供白名单求值器安全调用）。


def parse_money(value: Any, unit: str = "元") -> Any:
    """金额解析：成功返回规范化数值字符串，否则返回原值。"""
    if not isinstance(value, str):
        return value
    try:
        parsed = _try_money(value, unit)
    except (InvalidOperation, ValueError):
        return value
    return parsed[0] if parsed else value


def parse_time(value: Any) -> Any:
    """日期/时间解析：成功返回 ISO YYYY-MM-DD，否则返回原值。"""
    if not isinstance(value, str):
        return value
    try:
        parsed = _try_date(value)
    except (InvalidOperation, ValueError):
        return value
    return parsed[0] if parsed else value


def parse_number(value: Any) -> Any:
    """数值解析：整数优先、其次浮点；前导零/无法无损解析返回原值。"""
    if not isinstance(value, str):
        return value
    if re.match(r"^[+-]?0\d", _normalize_ws(value)):
        return value  # 前导零（0 后紧跟数字）：可能有编码语义，不猜
    canonical = _try_integer(value)
    if canonical is None:
        canonical = _try_float(value)
    return canonical if canonical is not None else value


def trim(value: Any) -> Any:
    """去除首尾空白（含换行）。"""
    return value.strip() if isinstance(value, str) else value


_TAG_RE = re.compile(r"<[^>]*>")


def clean_html(value: Any) -> Any:
    """剥离 HTML 标签并解码实体（标签间空白折叠）。"""
    if not isinstance(value, str):
        return value
    import html as _html

    cleaned = _TAG_RE.sub("", value)
    cleaned = _html.unescape(cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def regex_extract(value: Any, pattern: str, group: int = 1) -> Any:
    """正则抽取：命中返回捕获组，未命中/参数非法返回原值（safe_regex_search 防病态）。"""
    if not isinstance(value, str) or not isinstance(pattern, str):
        return value
    match = safe_regex_search(pattern, value)
    if not match or not match.groups():
        return value
    try:
        return str(match.group(group))
    except (IndexError, re.error):
        return value


def coalesce(*values: Any) -> Any:
    """返回第一个非空值（None / 空串 / 纯空白视为空）；全空返回 None。"""
    for item in values:
        if item is None:
            continue
        if isinstance(item, str) and not item.strip():
            continue
        return item
    return None


def concat(*values: Any, sep: str = "") -> str:
    """拼接各值（None 跳过）。"""
    return sep.join(str(item) for item in values if item is not None)
