"""值级数据变换 + 记录级后处理 CLI 后端（批 B-2 / 走查 R5.1，`omnicrawler transform`）。

安全约定：
- **默认不写文件**：无 --confirm 时输出统计（等价 dry-run），写文件必须显式
  --confirm（安全门）；--dry-run 额外展示前 N 条新旧列对照。
- 值级变换结果始终追加 ``{列名}_parsed`` 列，原列永不被改写（可回滚可对账）。
- 记录级后处理（--sort / --group-by / --agg，R5.1）**不改写入路径**：它的交付物就是
  写出物本身；有分组时写出的是聚合表，并同样受 --confirm 门约束。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..services.data_postprocess import (
    AggSpec,
    SortKey,
    parse_agg,
    parse_sort,
    render_numeric_advice,
)
from ..services.data_transform import build_specs, transform_file


def execute(
    source: str,
    target: str | None,
    *,
    maps: list[str] | None = None,
    transform_steps: str | None = None,
    sorts: Sequence[str] = (),
    group_by: Sequence[str] = (),
    aggregations: Sequence[str] = (),
    src_format: str | None = None,
    dst_format: str | None = None,
    dry_run: bool = False,
    confirm: bool = False,
    batch_size: int = 1000,
    max_records: int | None = None,
    on_error: str = "skip",
    preview_limit: int = 5,
) -> dict[str, Any]:
    """执行值级变换与（可选的）记录级后处理。confirm=True 时写 target，否则只预览/统计。"""
    sort_keys: list[SortKey] = [parse_sort(spec) for spec in sorts]
    agg_specs: list[AggSpec] = [parse_agg(spec) for spec in aggregations]
    has_post = bool(sort_keys or group_by or agg_specs)
    specs = build_specs(maps or (), transform_steps=transform_steps) if (maps or transform_steps) else []
    if not specs and not has_post:
        # 原先这条由 build_specs 抛（它只有"映射"这个概念）；R5.1 起命令多了两类操作，
        # 判据必须上移到**命令层**，否则 `--group-by 分类 --agg count` 会被误判成"没有操作"。
        raise ValueError(
            "transform 需要至少一个操作：--map / --transform-steps（值级）"
            "或 --sort / --group-by / --agg（记录级）"
        )
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
        sort_keys=sort_keys,
        group_by=group_by,
        aggregations=agg_specs,
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
    post = result.get("post_processing") or {}
    if post.get("post_processed"):
        # R5.1：摘要必须标注「本次含后处理」——交付物已经不是原始记录，而是排序/聚合结果。
        result["post_processing_summary"] = _post_processing_summary(post)
    if not write:
        result["note"] = "未写入文件：--confirm 执行写入，--dry-run 展示样例预览"
    # 走查 R2.2：**「表达式跑了但没改变任何值」必须说出来**。
    # `parse_money` 这类函数按契约「无法解析则返回原值」，既不算异常也不计入
    # eval_failures —— 只看 eval_failures 会得到 0，用户会以为清洗生效了。
    ineffective = result.get("ineffective_columns") or []
    if ineffective:
        result["notice_ineffective"] = (
            "以下输出列的值与原值完全相同（该清洗对这批数据没有生效）："
            + "、".join(str(name) for name in ineffective)
            + "。常见原因：函数遇到无法解析的值时按契约返回原值"
            "（例如 parse_money 遇到不认识的货币符号/格式）。请核对源列的实际取值形态。"
        )
    # 走查 R5.1 的"不静默"：列被判为文本 ⇒ 排序"看着排好了"、sum/avg 取不到数。
    # ★ 建议必须来自**实测探测**（conversion_recipes）：一个候选函数都修不好的列会被如实
    #   回报为"无法自动转换"，而不是一律甩一句 parse_number —— 后者会让用户白跑一轮。
    recipes = [
        (
            str(item.get("source", "")),
            str(item.get("function", "")),
            str(item.get("target", "")),
        )
        for item in (post.get("conversion_recipes") or [])
        if isinstance(item, dict)
    ]
    unconvertible = [str(name) for name in (post.get("unconvertible_columns") or [])]
    failing = [str(name) for name in (post.get("non_numeric_columns") or [])]
    agg_failing = [spec.column for spec in agg_specs if spec.column and spec.column in failing]
    advice = render_numeric_advice(
        recipes=recipes,
        unconvertible=unconvertible,
        action="对 sum/avg 求和" if agg_failing else "按数值排序",
    )
    if advice:
        result["notice_numeric_text"] = advice
    return result


def _post_processing_summary(post: dict[str, Any]) -> str:
    """交付摘要里那句「本次含后处理」——把做过什么、口径是什么、输入输出各多少行写清楚。"""
    parts: list[str] = []
    if post.get("group_by") or post.get("aggregations"):
        group = "、".join(post.get("group_by") or []) or "（全表）"
        aggs = "、".join(post.get("aggregations") or []) or "（仅分组键）"
        parts.append(f"按 {group} 分组，聚合项 {aggs}，得到 {post.get('groups')} 组")
    if post.get("sort_keys"):
        # 口径必须跟着排序键一起说出来："按文本排了数字列"是最容易看着排好了其实没排对的一种。
        kinds = post.get("sorted_as") or {}
        detail = "、".join(
            f"{column}（{'文本' if kind == 'text' else '数值'}口径）"
            for column, kind in kinds.items()
        )
        parts.append("排序 " + (detail or "、".join(post.get("sort_keys") or [])))
    if post.get("numeric_from_text_columns"):
        parts.append(
            "列 " + "、".join(post["numeric_from_text_columns"]) + " 的值本是文本，已按数值解读"
        )
    if post.get("non_numeric_cells"):
        parts.append(
            f"聚合时有 {post['non_numeric_cells']} 个取值取不到数值（已跳过，结果不含它们）"
        )
    return (
        f"本次含后处理：{'；'.join(parts)}。"
        f"输入 {post.get('rows_in')} 行 → 交付 {post.get('rows_out')} 行。"
    )
