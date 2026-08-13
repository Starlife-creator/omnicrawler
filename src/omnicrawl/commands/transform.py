"""值级数据变换 CLI 后端（批 B-2，`omnicrawl transform`）。

安全约定：
- **默认不写文件**：无 --confirm 时输出统计（等价 dry-run），写文件必须显式
  --confirm（安全门）；--dry-run 额外展示前 N 条新旧列对照。
- 变换结果始终追加 ``{列名}_parsed`` 列，原列永不被改写（可回滚可对账）。
"""

from __future__ import annotations

from typing import Any

from ..services.data_transform import build_specs, transform_file


def execute(
    source: str,
    target: str | None,
    *,
    maps: list[str] | None = None,
    transform_steps: str | None = None,
    src_format: str | None = None,
    dst_format: str | None = None,
    dry_run: bool = False,
    confirm: bool = False,
    batch_size: int = 1000,
    max_records: int | None = None,
    on_error: str = "skip",
    preview_limit: int = 5,
) -> dict[str, Any]:
    """执行值级变换。confirm=True 时写 target，否则只预览/统计。"""
    specs = build_specs(maps or (), transform_steps=transform_steps)
    write = bool(confirm) and not bool(dry_run)
    if write and not target:
        raise ValueError("transform 写文件需要目标文件（positional target）")
    result = transform_file(
        source,
        target if write else None,
        specs,
        src_format=src_format,
        dst_format=dst_format,
        batch_size=batch_size,
        max_records=max_records,
        on_error=on_error,
        preview_limit=preview_limit if dry_run else 0,
    )
    result["mode"] = "write" if write else "dry-run"
    result["maps"] = [
        {
            "column": spec.column,
            "expression": spec.expression,
            "output_column": spec.output_column,
        }
        for spec in specs
    ]
    if not write:
        result["note"] = "未写入文件：--confirm 执行写入，--dry-run 展示样例预览"
    return result
