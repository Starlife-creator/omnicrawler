"""记录级数据后处理：排序与分组聚合（走查 R5.1）。

## 为什么是纯 Python，而不是下推 DuckDB

《优化方案》§6.5 R5.1 初稿写的是「直接下推 SQL，不新造聚合实现」，前提是"完整依赖集含 DuckDB"。
取证后改为纯 Python，理由三条（记在 §6.5 的实施校正里）：

1. `duckdb` 位于 **`storage` 可选 extra**（`pyproject.toml`），而 `tools/check_minimal_install.py`
   的定位是「**核心能力不依赖可选 extras**」；而"按 X 排序 / 按 Y 分组 / 统计"是走查里
   **需求频次最高的一类** ⇒ 做成 extras 门控等于把核心能力降级。
2. **没有规模收益**：调用方 `services/data_transform.py::transform_file` 已经把全部记录读入内存
   （`READERS[...]` 返回可切片序列），下推 SQL 省不掉读入。要省就得绕开 READERS 让 DuckDB
   直读文件 —— 那是**第二条读路径**（第二真源），还会丢掉 `--on-error` / 预览 / 取消等既有语义。
3. 有 duckdb 走 SQL、没有走 Python ＝ **两条实现必然漂移**（R4.1 刚用"两条路径必须共用一处实现"
   钉过同一个问题）。

## 数值口径：文本也是数（本模块是**唯一判据处**）

`quality/normalizers.py` 的值级函数**一律文本进文本出**（契约："解析失败返回原值（无损）"），
`convertx` 的 CSV 读入也**不做类型推断** ⇒ **管道里根本没有 Python 数值**。
若这里只认 ``int/float``，``sum``/``avg`` 就是**死功能**：用户每一步都做对了
（``--map "价格 = parse_money(价格)"``），拿到手的仍是字符串 ``'51.77'``，聚合结果恒为 ``None``。
（0.13.0 走查实测：``sum``/``avg`` 全部取不到值、结果为空。）

所以"什么样的文本可以当数值读"只在本模块定义一次：

- **严格十进制字面量**（``51.77`` / ``-3`` / ``.5``，含前导零 ``007``）⇒ **可读**。
  这是无歧义的算术解读，不是格式猜测。
- **其余一律不可读**：``1,234``（千分位与小数逗号有歧义）、``£51.77``（带符号）、``1e5``、
  ``N/A`` ⇒ **跳过 + 计数 + 回报**，并给一条**探测得出**的可复制补救命令
  （`conversion_recipes`：先试 ``parse_number``、再试 ``parse_money``，**一个都修不好就如实说**）。

被按数值解读过的文本列记进 ``PostProcessStats.numeric_from_text_columns`` ——
"文本被当数字用了"必须看得见。

## 语义（每一条都**必须可见**，不许静默）

- **空值恒排最后**（升序降序都一样）；**比较口径由该列的实际取值决定**（全可读 ⇒ 数值比较，
  否则文本比较），结果回写到 ``sorted_as`` —— 「价格」若是文本列，排序"看着像排好了"其实不是，
  用户必须看得见，并且（能修的话）拿到一条**指名道姓**的补救提示。
- **分组是划分**：各组行数之和必须等于输入行数，这是硬不变量（``grouped_rows``，防静默丢行）。
- ``sum``/``avg`` **只接受可读的数值**；被跳过的单元格**计数并回报**
  （沿用 R2.2 的"跑了但没生效必须可见"）。★ ``None`` 不计入"非数值"——
  缺值是缺失、不是类型错误，与 SQL 的 ``SUM`` 忽略 NULL 一致；把 NULL 报成"非数值"就是喊狼来了。
- **分组键与去重键故意不同口径**：``--group-by`` 用**原始值**（分组键是给人看的标签，
  ``007`` 不能被显示成 ``7``——那是信息丢失）；``count_distinct`` 用**数值键**（它只输出个数，
  没有标签可丢，``1.5`` 与 ``1.50`` 本就该算同一个值）。

## 不做（与 §6.3「通用统计框架」一致）

不建查询语言 / 不建插件式算子 / 不加配置段：只有"排序"与"分组聚合"两件事，
由既有 `transform` 命令的一个新参数面完成。扩展 `AGG_FUNCTIONS` 时必须同时补语义与守卫。
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from ..quality.normalizers import parse_money, parse_number

#: 聚合函数白名单。★ 只放**能一句话说清语义**的几个；扩这里＝扩接口，必须同时补语义与守卫。
AGG_FUNCTIONS: tuple[str, ...] = ("count", "count_distinct", "sum", "avg", "min", "max")

#: ``--map`` 追加列的后缀。**唯一真源**：`services/data_transform.py` 从这里再导出
#: （同一对象，因此不可能漂移），本模块用它把"失败列本就是 --map 的产物"识别出来。
PARSED_SUFFIX = "_parsed"

#: 需要数值的聚合（其余可按文本比较）。
_NUMERIC_AGGREGATIONS: frozenset[str] = frozenset({"sum", "avg"})

#: 可安全按数值解读的**严格十进制字面量**。刻意不含千分位、货币符号、科学计数、``inf``/``nan``。
_STRICT_NUMBER_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d+)?|\.\d+)$")

#: 补救探测的候选函数 —— **必须**是 `core.ast_evaluator.ALLOWED_FUNCTIONS` 里的名字，
#: 否则建议的命令在 `--map` 里根本跑不起来。
_CANDIDATE_TYPERS: tuple[tuple[str, Callable[[Any], Any]], ...] = (
    ("parse_number", parse_number),
    ("parse_money", parse_money),
)

_AGG_RE = re.compile(
    r"^(?P<func>[a-z_]+)\s*(?:\(\s*(?P<column>[^()]*?)\s*\))?\s*(?::\s*(?P<alias>.+?)\s*)?$",
    re.IGNORECASE,  # 函数名大小写不敏感：`MAX(价格)` 与 `max(价格)` 是同一件事
)


# ── 规格解析 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SortKey:
    """一个排序键。``--sort "列名[:asc|:desc]"``（缺省 asc）。"""

    column: str
    descending: bool = False

    @property
    def direction(self) -> str:
        return "desc" if self.descending else "asc"

    @property
    def label(self) -> str:
        return f"{self.column} {self.direction}"


@dataclass(frozen=True, slots=True)
class AggSpec:
    """一个聚合项。``--agg "函数(列名)[:输出别名]"``。

    ``count`` 不带列名 ⇒ 行数；``count(列名)`` **明确拒绝** —— "计的是行还是非空值"
    含糊，宁可让用户写清楚（``count`` 或 ``count_distinct(列名)``）。
    """

    func: str
    column: str = ""
    alias: str = ""

    @property
    def output_column(self) -> str:
        if self.alias:
            return self.alias
        if not self.column:
            return self.func
        return f"{self.func}_{self.column}"

    @property
    def label(self) -> str:
        inner = f"({self.column})" if self.column else ""
        return f"{self.func}{inner}"


def parse_sort(spec: str) -> SortKey:
    """解析 ``"列名[:asc|:desc]"``。

    ★ 用 `rpartition` + 白名单判定方向，而不是"按冒号切两半" —— 列名本身含冒号时
    后者会把它切坏（``"a:b"`` 会被当成"列 a、方向 b"）。
    """
    text = (spec or "").strip()
    if not text:
        raise ValueError("--sort 需要 '列名[:asc|:desc]' 格式")
    column, sep, tail = text.rpartition(":")
    if sep and tail.strip().lower() in {"asc", "desc"}:
        direction = tail.strip().lower()
    else:
        column, direction = text, "asc"
    column = column.strip()
    if not column:
        raise ValueError(f"--sort 列名不能为空: {spec!r}")
    return SortKey(column=column, descending=direction == "desc")


def parse_agg(spec: str) -> AggSpec:
    """解析 ``"函数(列名)[:输出别名]"``。

    Raises:
        ValueError: 函数不在白名单、缺少必要列名、或写了含糊的 ``count(列名)``。
    """
    text = (spec or "").strip()
    if not text:
        raise ValueError("--agg 需要 '函数(列名)[:别名]' 格式")
    match = _AGG_RE.match(text)
    if match is None:
        raise ValueError(f"--agg 无法解析: {spec!r}（期望 '函数(列名)[:别名]'）")
    func = match.group("func").lower()
    column = (match.group("column") or "").strip()
    alias = (match.group("alias") or "").strip()
    if func not in AGG_FUNCTIONS:
        raise ValueError(f"--agg 不支持的聚合函数 {func!r}；可用: {list(AGG_FUNCTIONS)}")
    if func == "count" and column:
        raise ValueError(
            f"--agg 的 count 不接受列名（{spec!r}）：计行数请用 `count`，"
            f"计该列的去重值个数请用 `count_distinct({column})`"
        )
    if func != "count" and not column:
        raise ValueError(f"--agg 的 {func} 需要一个列名，例如 `{func}(金额)`")
    if alias in {"count", "count_distinct"} and func != alias:
        raise ValueError(f"--agg 别名 {alias!r} 与内置列名冲突，请换一个")
    return AggSpec(func=func, column=column, alias=alias)


# ── 数值解读：本模块唯一的"文本也是数"判据处 ────────────────────────────


def _numeric_value(value: Any) -> Decimal | None:
    """把值读成数值；**读不出来就是读不出来**（返回 ``None``，绝不猜）。

    - ``bool`` ⇒ ``None``（``True``/``False`` 参与求和只会误导）。
    - 一律走 ``Decimal``：金额聚合用二进制浮点会留下 ``51.870000000000005`` 这类伪影，
      而这里的取值来源本就是**十进制字面量文本**，用十进制做算术是唯一忠实的做法。
      浮点输入用 ``str()`` 中转，避免 ``Decimal(0.1)`` 展开成二进制长尾。
    - ``str`` ⇒ 仅在**严格十进制字面量**时可读（见模块文档）。
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value)) if math.isfinite(value) else None
    if isinstance(value, str):
        text = value.strip()
        if _STRICT_NUMBER_RE.match(text) is None:
            return None
        try:
            number = Decimal(text)
        except InvalidOperation:  # pragma: no cover - 正则已排除，兜底不外抛
            return None
        return number if number.is_finite() else None
    return None


def is_numeric_readable(value: Any) -> bool:
    """该值能否被按数值读（供外部判据复用，不重复实现）。"""
    return _numeric_value(value) is not None


def _number_key(value: Any) -> Decimal:
    """比较/去重用的数值键（调用前提：该列已判定为 numeric）。"""
    number = _numeric_value(value)
    return Decimal(0) if number is None else number


def column_kind(values: Iterable[Any]) -> str:
    """按**实际取值**决定比较与算术的口径：全部可读 ⇒ ``numeric``，否则 ``text``。

    空列（全为 ``None``）判 ``numeric``（无值可比，取哪种都不影响结果）。
    """
    present = [value for value in values if value is not None]
    if not present:
        return "numeric"
    return "numeric" if all(_numeric_value(value) is not None for value in present) else "text"


def _safe_apply(func: Callable[[Any], Any], value: Any) -> Any:
    """探测候选函数时**任何异常都不得影响主流程**（normalizers 契约本就"不外抛"，双保险）。"""
    try:
        return func(value)
    except Exception:  # noqa: BLE001 - 探测路径，异常一律视为"修不好"
        return value


def conversion_recipes(
    records: Sequence[dict[str, Any]], columns: Iterable[str]
) -> tuple[tuple[tuple[str, str, str], ...], tuple[str, ...]]:
    """对"取不到数值"的列，**探测**哪个 ``--map`` 函数真能修好它，并算出可复制的映射。

    ★ 建议必须来自实测：把该列**每一个**不可读的取值都喂给候选函数，
    只有"全体都能修好"的函数才会被推荐。一个候选都修不好 ⇒ 归入 ``unconvertible``，
    **不编造建议** —— 否则用户会照着一条无效命令白跑一轮（R4.1 的"不撒谎"）。

    ★ 目标列名也算准，不做"再加一层后缀"的机械动作：若失败列本身已是 ``--map`` 的产物
    （``价格_parsed``）且它的源列还在（``价格``），说明用户**换错了函数**
    （例如对货币列用了 ``parse_number``，而 ``parse_number`` 按契约原样返回 ``£51.77``）——
    此时正确的做法是**对源列换函数重做**，而不是产出 ``价格_parsed_parsed`` 这种绕路命名的列。

    Args:
        records: 全部记录。
        columns: 需要数值、但取不到数值的列名。

    Returns:
        ``(recipes, unconvertible)``；``recipes`` 的每条为
        ``(要转换的列, 函数名, 转完后可供排序/聚合的列名)``。
    """
    present = available_columns(records)
    recipes: list[tuple[str, str, str]] = []
    unconvertible: list[str] = []
    for column in dict.fromkeys(columns):
        if column not in present:
            continue
        values = [record.get(column) for record in records]
        bad = [value for value in values if value is not None and _numeric_value(value) is None]
        if not bad:
            continue
        working = [
            name
            for name, func in _CANDIDATE_TYPERS
            if all(_numeric_value(_safe_apply(func, value)) is not None for value in bad)
        ]
        if not working:
            unconvertible.append(column)
            continue
        base = column[: -len(PARSED_SUFFIX)] if column.endswith(PARSED_SUFFIX) else ""
        if base and base in present:
            recipes.append((base, working[0], column))  # 换函数重做 ⇒ 目标列就是当前这列
        else:
            recipes.append((column, working[0], f"{column}{PARSED_SUFFIX}"))
    return tuple(recipes), tuple(unconvertible)


def numeric_from_text_columns(
    records: Sequence[dict[str, Any]], columns: Iterable[str]
) -> tuple[str, ...]:
    """给出的列里「值本来是文本、却被按数值解读」的那些（可见性回执）。

    它**不参与**任何读取决定，只回答一个问题：这次算术/排序在文本列上做了数值解读吗？
    """
    out: list[str] = []
    for column in dict.fromkeys(columns):
        values = [record.get(column) for record in records]
        if column_kind(values) != "numeric":
            continue
        if any(isinstance(value, str) and _numeric_value(value) is not None for value in values):
            out.append(column)
    return tuple(out)


def available_columns(records: Sequence[dict[str, Any]]) -> tuple[str, ...]:
    """记录里出现过的列（按首次出现顺序）—— 报错时用来告诉用户**有哪些列**。"""
    seen: dict[str, None] = {}
    for record in records:
        for key in record:
            seen.setdefault(str(key), None)
    return tuple(seen)


def _require_columns(wanted: Sequence[str], present: Sequence[str], *, what: str) -> None:
    missing = [name for name in wanted if name not in present]
    if missing:
        raise ValueError(f"{what} 引用了不存在的列 {missing}；该数据实际有: {list(present)}")


# ── 比较器：空值恒排最后 + 按列口径 ──────────────────────────────────────


class _Comparable:
    """单键比较器。

    - **空值（``None``）恒排最后**：升序降序都不改变这一点（"空值不小于任何东西"）。
    - ``kind == "numeric"`` ⇒ 按**数值键**比较（文本列里的 ``"9"`` 不会被排到 ``"10"`` 后面）；
      否则按 ``str`` 比较。
    - ``descending`` 只反转**非空值**之间的次序。
    """

    __slots__ = ("descending", "is_null", "key")

    descending: bool
    is_null: bool
    key: Any

    def __init__(self, value: Any, kind: str, descending: bool) -> None:
        self.is_null = value is None
        self.descending = descending
        self.key = _number_key(value) if kind == "numeric" else ("" if value is None else str(value))

    def __lt__(self, other: _Comparable) -> bool:
        if self.is_null:
            return False
        if other.is_null:
            return True
        if self.descending:
            return bool(other.key < self.key)
        return bool(self.key < other.key)


# ── 排序 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class SortReceipt:
    """排序回执：口径 + "文本被当数字用了"的可见性 + 补救建议。"""

    #: ``{列名: "numeric"|"text"}`` —— 让"按文本排了数字列"这件事可见。
    kinds: dict[str, str]
    numeric_from_text_columns: tuple[str, ...] = ()
    conversion_recipes: tuple[tuple[str, str, str], ...] = ()
    unconvertible_columns: tuple[str, ...] = ()


def sort_records(
    records: Sequence[dict[str, Any]], keys: Sequence[SortKey]
) -> tuple[list[dict[str, Any]], SortReceipt]:
    """多键稳定排序。**按给出顺序依次生效**（先出现的键为主键）。

    Raises:
        ValueError: 引用了不存在的列。
    """
    if not keys:
        return list(records), SortReceipt(kinds={})
    ordered = list(records)
    present = available_columns(ordered)
    _require_columns([key.column for key in keys], present, what="--sort")
    columns = [key.column for key in keys]
    kinds = {column: column_kind(record.get(column) for record in ordered) for column in columns}
    # 稳定排序 ⇒ 从最后一个键开始依次排，得到"多键按给出顺序生效"的语义。
    for key in reversed(keys):
        ordered.sort(
            key=lambda record, _key=key: _Comparable(  # type: ignore[misc]
                record.get(_key.column), kinds[_key.column], _key.descending
            )
        )
    # 只有"判为文本"的列才可能"看着排好了其实没排对"，因此只对它们探测补救。
    text_columns = [column for column in columns if kinds[column] == "text"]
    recipes, unconvertible = conversion_recipes(ordered, text_columns)
    return ordered, SortReceipt(
        kinds=kinds,
        numeric_from_text_columns=numeric_from_text_columns(ordered, columns),
        conversion_recipes=recipes,
        unconvertible_columns=unconvertible,
    )


# ── 分组聚合 ─────────────────────────────────────────────────────────────


@dataclass(slots=True)
class PostProcessStats:
    """后处理回执。**每一条都是可机检的**（守卫直接断言这里的数字）。"""

    rows_in: int
    rows_out: int
    sort_keys: tuple[str, ...] = ()
    sorted_as: dict[str, str] = field(default_factory=dict)
    group_by: tuple[str, ...] = ()
    aggregations: tuple[str, ...] = ()
    groups: int = 0
    #: 各组行数之和。分组是**划分**：它必须等于 ``rows_in``（防静默丢行）。
    grouped_rows: int = 0
    #: ``sum``/``avg`` 跳过（非 None 且不可读）的单元格数与涉及的列（"跑了但没生效"必须可见）。
    non_numeric_cells: int = 0
    non_numeric_columns: tuple[str, ...] = ()
    #: 值本是文本、却被按数值解读的列（"文本被当数字用了"必须可见）。
    numeric_from_text_columns: tuple[str, ...] = ()
    #: 取不到数值的列 → **探测得出**的可复制补救映射
    #: ``((要转换的列, 函数名, 转完后的列名), ...)``。
    conversion_recipes: tuple[tuple[str, str, str], ...] = ()
    #: ★ 一个候选函数都修不好的列：如实回报，绝不编造建议。
    unconvertible_columns: tuple[str, ...] = ()
    failure_samples: list[str] = field(default_factory=list)

    @property
    def post_processed(self) -> bool:
        """本次是否含后处理（交付摘要里要标注）。"""
        return bool(self.sort_keys or self.group_by or self.aggregations)

    @property
    def partition_ok(self) -> bool:
        return self.grouped_rows == self.rows_in

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows_in": self.rows_in,
            "rows_out": self.rows_out,
            # ★ `post_processed` 是**计算属性**，容易漏；漏了会让命令层"本次含后处理"
            #   的摘要永远不生成（实测踩过：摘要恒 None、用户看不到自己拿到的是聚合表）。
            "post_processed": self.post_processed,
            "sort_keys": list(self.sort_keys),
            "sorted_as": dict(self.sorted_as),
            "group_by": list(self.group_by),
            "aggregations": list(self.aggregations),
            "groups": self.groups,
            "grouped_rows": self.grouped_rows,
            "partition_ok": self.partition_ok,
            "non_numeric_cells": self.non_numeric_cells,
            "non_numeric_columns": list(self.non_numeric_columns),
            "numeric_from_text_columns": list(self.numeric_from_text_columns),
            "conversion_recipes": [
                {"source": source, "function": func, "target": target}
                for source, func, target in self.conversion_recipes
            ],
            "unconvertible_columns": list(self.unconvertible_columns),
            "failure_samples": self.failure_samples,
        }


def _distinct(values: Sequence[Any], kind: str) -> int:
    """去重个数。``numeric`` 列按**数值键**去重（``1.5`` 与 ``1.50`` 是同一个值）。

    这里可以用数值键而 ``--group-by`` 不行：它只输出**个数**，没有标签可丢。
    """
    seen: set[Any] = set()
    for value in values:
        if value is None:
            continue
        seen.add(_number_key(value) if kind == "numeric" else str(value))
    return len(seen)


def _numeric_pick(values: Sequence[Any]) -> tuple[list[Decimal], list[Any]]:
    """``sum``/``avg`` 的取值口径。

    Returns:
        ``(可读的数值, 被跳过的取值)``。★ ``None`` **不**计入"被跳过" ——
        缺值是缺失、不是类型错误（与 SQL 忽略 NULL 一致）。
    """
    picked: list[Decimal] = []
    skipped: list[Any] = []
    for value in values:
        if value is None:
            continue
        number = _numeric_value(value)
        if number is None:
            skipped.append(value)
        else:
            picked.append(number)
    return picked, skipped


def _emit(value: Decimal) -> float | Decimal:
    """交付口径：内部用 ``Decimal`` 精确算，**只在最后一步**落到 ``float``。

    ``float(Decimal("155.61"))`` 是干净的 ``155.61``；伪影（``51.870000000000005``）
    来自"用二进制浮点累加十进制字面量"，而不是来自这一次转换。
    ``float`` 装不下（溢出）时**退回 Decimal 原文**：宁可交付一个精确的十进制字符串，
    也不交付 ``inf``（JSON 侧 `default=str` 会把它写成字符串，仍可读、不撒谎）。
    """
    number = float(value)
    return number if math.isfinite(number) else value


def _extreme(values: Sequence[Any], kind: str, *, want_max: bool) -> Any:
    """``min``/``max``：按列口径挑极值，但**返回原始取值**（忠实于数据形态）。"""
    present = [value for value in values if value is not None]
    if not present:
        return None
    chooser = max if want_max else min
    if kind == "numeric":
        return chooser(present, key=_number_key)
    return chooser(present, key=lambda value: str(value))


def aggregate_records(
    records: Sequence[dict[str, Any]],
    group_by: Sequence[str],
    aggregations: Sequence[AggSpec],
) -> tuple[list[dict[str, Any]], PostProcessStats]:
    """按 ``group_by`` 分组，对每组求 ``aggregations``。

    ``group_by`` 为空 ⇒ 全表一个组（"统计总数"这类需求）。
    ``group_by`` 非空而 ``aggregations`` 为空 ⇒ 去重后的分组键（SQL ``GROUP BY`` 无聚合）。

    Raises:
        ValueError: 引用了不存在的列。
    """
    present = available_columns(records)
    _require_columns(list(group_by), present, what="--group-by")
    _require_columns(
        [spec.column for spec in aggregations if spec.column], present, what="--agg"
    )

    # ★ 分组键用**原始值**：它是给人看的标签，"007" 不能被显示成 "7"。
    buckets: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for record in records:
        buckets.setdefault(tuple(record.get(name) for name in group_by), []).append(record)

    key_kinds = [column_kind(record.get(name) for record in records) for name in group_by]
    non_numeric_cells = 0
    failing_columns: list[str] = []
    samples: list[str] = []
    rows: list[dict[str, Any]] = []
    for key in sorted(
        buckets,
        key=lambda item: tuple(  # 输出按分组键升序 ⇒ 结果确定、可比对
            _Comparable(value, kind, False) for value, kind in zip(item, key_kinds, strict=True)
        ),
    ):
        members = buckets[key]
        row: dict[str, Any] = dict(zip(group_by, key, strict=True))
        for spec in aggregations:
            values = [member.get(spec.column) for member in members] if spec.column else []
            if spec.func == "count":
                row[spec.output_column] = len(members)
            elif spec.func == "count_distinct":
                row[spec.output_column] = _distinct(
                    values, column_kind(values) if values else "numeric"
                )
            elif spec.func in _NUMERIC_AGGREGATIONS:
                picked, skipped = _numeric_pick(values)
                if skipped:
                    non_numeric_cells += len(skipped)
                    failing_columns.append(spec.column)
                    if len(samples) < 5:
                        samples.append(
                            f"{spec.label}: 该列有 {len(skipped)} 个不可读的取值被跳过"
                            f"（例如 {skipped[0]!r}）"
                        )
                if not picked:
                    row[spec.output_column] = None
                elif spec.func == "sum":
                    row[spec.output_column] = _emit(sum(picked, Decimal(0)))
                else:
                    row[spec.output_column] = _emit(sum(picked, Decimal(0)) / len(picked))
            else:  # min / max
                row[spec.output_column] = _extreme(
                    values, column_kind(values) if values else "numeric", want_max=spec.func == "max"
                )
        rows.append(row)

    numeric_columns = [spec.column for spec in aggregations if spec.func in _NUMERIC_AGGREGATIONS]
    recipes, unconvertible = conversion_recipes(records, failing_columns)
    stats = PostProcessStats(
        rows_in=len(records),
        rows_out=len(rows),
        group_by=tuple(group_by),
        aggregations=tuple(spec.label for spec in aggregations),
        groups=len(rows),
        grouped_rows=sum(len(members) for members in buckets.values()),
        non_numeric_cells=non_numeric_cells,
        non_numeric_columns=tuple(sorted(set(failing_columns))),
        numeric_from_text_columns=numeric_from_text_columns(
            records, [column for column in numeric_columns if column not in failing_columns]
        ),
        conversion_recipes=recipes,
        unconvertible_columns=unconvertible,
        failure_samples=samples,
    )
    return rows, stats


# ── 建议的渲染（在命令层调用）────────────────────────────────────────────


def render_numeric_advice(
    *,
    recipes: Sequence[tuple[str, str, str]],
    unconvertible: Sequence[str],
    action: str,
) -> str | None:
    """把探测结果拼成**可复制**的命令；修不好就如实说，不给空头支票。

    Args:
        recipes: ``((要转换的列, 函数名, 转完后的列名), ...)``，来自 `conversion_recipes`。
        unconvertible: 一个候选函数都修不好的列。
        action: 人类可读的动作，如 ``"按数值排序"`` / ``"对 sum/avg 求和"``。
    """
    if not recipes and not unconvertible:
        return None
    parts: list[str] = []
    if recipes:
        commands = "；".join(
            f'--map "{source} = {func}({source})"' for source, func, _ in recipes
        )
        targets = "、".join(target for _, _, target in recipes)
        parts.append(
            f"以下列被判为文本，无法{action} —— 请先做值级转换：{commands}，"
            f"再把排序/聚合的列名改成 {targets}。"
        )
    if unconvertible:
        parts.append(
            f"列 {list(unconvertible)} 的取值无法自动转换成数值"
            "（既非数字、也不是 parse_number/parse_money 能识别的形态），"
            "需要先人工确认数据本身是不是数值列。"
        )
    return "".join(parts)


__all__ = [
    "AGG_FUNCTIONS",
    "PARSED_SUFFIX",
    "AggSpec",
    "PostProcessStats",
    "SortKey",
    "SortReceipt",
    "aggregate_records",
    "available_columns",
    "column_kind",
    "conversion_recipes",
    "is_numeric_readable",
    "numeric_from_text_columns",
    "parse_agg",
    "parse_sort",
    "render_numeric_advice",
    "sort_records",
]
