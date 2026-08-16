"""值级数据变换服务（批 B-2）。

核心契约（H5 决策）：
- ``--map "lhs = expr"``：LHS 为目标列名，**默认追加 ``{lhs}_parsed`` 列，
  永不复写原列**（原列是证据，变换结果另列存放，可回滚可对账）。
- 表达式经 ``core.ast_evaluator.safe_eval`` 白名单求值（仅 normalizers 公开
  值级函数 + 运算符 + 字面量），无副作用、解析失败返回原值。
- 单行求值失败：该单元格写 None 并计入统计，不中断整批（值级契约）。
- ``--transform-steps`` 兼容：把旧「步骤列表」值级翻译为等价 ``--map``
  表达式；记录级算子（dedupe 等）不在白名单 → 明确报错走插件。
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..convertx import READERS, WRITERS, sniff_format
from ..core.ast_evaluator import ALLOWED_FUNCTIONS, safe_eval
from ..security.paths import require_workspace_path

#: 追加列的后缀（永不覆盖原列）
PARSED_SUFFIX = "_parsed"


@dataclass(frozen=True, slots=True)
class MapSpec:
    """一条变换映射：目标列名 + 值级表达式。"""

    column: str
    expression: str

    @property
    def output_column(self) -> str:
        return f"{self.column}{PARSED_SUFFIX}"


@dataclass(slots=True)
class TransformStats:
    rows: int
    columns_added: tuple[str, ...]
    eval_failures: int
    failure_samples: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rows": self.rows,
            "columns_added": list(self.columns_added),
            "eval_failures": self.eval_failures,
            "failure_samples": self.failure_samples,
        }


def parse_map(mapping: str) -> MapSpec:
    """解析 ``"lhs = expr"`` 为 MapSpec。"""
    if "=" not in mapping:
        raise ValueError(f"--map 需要 '列名 = 表达式' 格式: {mapping!r}")
    lhs, _, expr = mapping.partition("=")
    lhs = lhs.strip()
    expr = expr.strip()
    if not lhs:
        raise ValueError(f"--map 目标列名不能为空: {mapping!r}")
    if not expr:
        raise ValueError(f"--map 表达式不能为空: {mapping!r}")
    return MapSpec(column=lhs, expression=expr)


def _literal(value: Any) -> str:
    """表达式内联字面量：字符串带引号，其余按 repr。"""
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    return repr(value)


def translate_steps(steps: list[dict[str, Any]]) -> list[MapSpec]:
    """把旧 transform 步骤列表值级翻译为 MapSpec。

    仅支持白名单内（可值级）算子；记录级算子（dedupe 等）明确报错并提示
    使用插件 transformers。
    """
    specs: list[MapSpec] = []
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError(f"transform 步骤必须是对象: {step!r}")
        step_type = str(step.get("type", "")).strip()
        field_name = str(step.get("field") or step.get("column") or "").strip()
        if not field_name:
            raise ValueError(f"transform 步骤 {step_type!r} 缺少 field")
        if step_type not in ALLOWED_FUNCTIONS:
            raise ValueError(
                f"transform 步骤 {step_type!r} 不是值级算子；记录级操作（如 dedupe）请改用插件 transformers"
            )
        raw_options = step.get("options")
        options: dict[str, Any] = raw_options if isinstance(raw_options, dict) else {}
        if step_type == "regex_extract":
            pattern = options.get("pattern") or options.get("regex")
            if not pattern:
                raise ValueError("regex_extract 步骤需要 options.pattern")
            group = options.get("group", 1)
            expression = (
                f"regex_extract({_literal(field_name)}, {_literal(pattern)}, {_literal(group)})"
            )
        elif step_type == "parse_money" and options.get("unit") is not None:
            expression = f"parse_money({_literal(field_name)}, {_literal(options['unit'])})"
        elif step_type == "coalesce":
            fields = [field_name, *[str(value) for value in options.get("fields", [])]]
            expression = f"coalesce({', '.join(_literal(value) for value in fields)})"
        else:
            expression = f"{step_type}({_literal(field_name)})"
        specs.append(MapSpec(column=field_name, expression=expression))
    return specs


def build_specs(
    maps: Iterable[str] = (),
    *,
    transform_steps: str | None = None,
) -> list[MapSpec]:
    """合并 ``--map`` 列表与 ``--transform-steps`` 翻译结果。

    Args:
        maps: ``"lhs = expr"`` 字符串列表。
        transform_steps: JSON 数组字符串（或 ``@file`` 路径）。

    Raises:
        ValueError: 无任何映射、steps 非数组、算子非值级、步骤缺参。
    """
    specs = [parse_map(mapping) for mapping in maps]
    if transform_steps:
        steps = _load_steps(transform_steps)
        specs.extend(translate_steps(steps))
    if not specs:
        raise ValueError("transform 需要至少一个 --map 或 --transform-steps")
    return specs


def _load_steps(value: str) -> list[dict[str, Any]]:
    raw: Any = value
    if value.startswith("@"):
        # B08-004：@file 引用是外部可控路径，必须位于 CWD（工作区）内。
        raw = require_workspace_path(
            value[1:], root=Path.cwd(), what="--transform-steps @file"
        ).read_text(encoding="utf-8")
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise ValueError(f"--transform-steps 不是合法 JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError("--transform-steps 必须是 JSON 数组")
    return [item for item in data if isinstance(item, dict)]


def transform_records(
    records: list[dict[str, Any]],
    specs: list[MapSpec],
    *,
    batch_size: int = 1000,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[list[dict[str, Any]], TransformStats]:
    """逐行求值并追加 ``{lhs}_parsed`` 列（原列永不修改）。

    Returns:
        (新记录列表, 统计)。求值失败单元格写 None 并计入 eval_failures。
    """
    columns_added = tuple(spec.output_column for spec in specs)
    failures = 0
    samples: list[str] = []
    total = len(records)
    for start in range(0, total, max(1, batch_size)):
        batch = records[start : start + batch_size]
        for record in batch:
            for spec in specs:
                try:
                    record[spec.output_column] = safe_eval(spec.expression, record)
                except Exception as exc:  # noqa: BLE001 —— 单值失败不中断批次
                    record[spec.output_column] = None
                    failures += 1
                    if len(samples) < 5:
                        samples.append(f"{spec.expression}: {type(exc).__name__}: {exc}")
        if on_progress is not None:
            on_progress(min(start + batch_size, total), total)
    stats = TransformStats(
        rows=total,
        columns_added=columns_added,
        eval_failures=failures,
        failure_samples=samples,
    )
    return records, stats


def transform_file(
    src: str | Path,
    dst: str | Path | None,
    specs: list[MapSpec],
    *,
    src_format: str | None = None,
    dst_format: str | None = None,
    batch_size: int = 1000,
    max_records: int | None = None,
    on_error: str = "skip",
    on_progress: Callable[[int, int], None] | None = None,
    preview_limit: int = 0,
) -> dict[str, Any]:
    """读取数据文件 → 值级变换 → 写出（复用 ConvertX 读写器）。

    dst 为 None 或 dry-run 时只变换不写；preview_limit>0 时返回前 N 条
    「原列 + 新列」对照（仅涉及变换的列）。
    """
    src_path = Path(src)
    if not src_path.is_file():
        raise FileNotFoundError(f"transform: 源文件不存在: {src_path}")
    src_fmt = src_format or sniff_format(src_path)
    if src_fmt not in READERS:
        raise ValueError(f"transform: 不支持的源格式 {src_fmt!r}（已注册: {sorted(READERS)}）")
    records = READERS[src_fmt](src_path, {"on_error": on_error})
    if max_records is not None:
        records = records[:max_records]
    transformed, stats = transform_records(records, specs, batch_size=batch_size, on_progress=on_progress)
    written = False
    output_path: str | None = None
    if dst is not None:
        dst_path = Path(dst)
        dst_fmt = dst_format or sniff_format(dst_path)
        if dst_fmt not in WRITERS:
            raise ValueError(f"transform: 不支持的目标格式 {dst_fmt!r}（已注册: {sorted(WRITERS)}）")
        WRITERS[dst_fmt](transformed, dst_path, {"on_error": on_error})
        written = True
        output_path = str(dst_path)
    preview: list[dict[str, Any]] = []
    if preview_limit > 0:
        involved = {spec.column for spec in specs} | set(stats.columns_added)
        preview = [
            {key: value for key, value in record.items() if key in involved}
            for record in transformed[:preview_limit]
        ]
    return {
        "source": str(src_path),
        "output": output_path,
        "written": written,
        "preview": preview,
        **stats.to_dict(),
    }


__all__ = [
    "MapSpec",
    "PARSED_SUFFIX",
    "TransformStats",
    "build_specs",
    "parse_map",
    "transform_file",
    "transform_records",
    "translate_steps",
]
