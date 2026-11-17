"""ConvertX 转换边图与最短路（对齐成熟转换工具的 ``RoutePlanner`` 语义）。

先说明一个本仓特有的事实：convertx 的 Reader/Writer 共享 ``CanonicalRecords`` /
``DocumentIR`` 中间表示，因此「任意可读格式 → 任意可写格式」本身就是一条直连边。
刻意绕中间文件不会更可靠，只会增加一次落盘与有损风险。所以本模块的默认图是
**格式级直连边**，用于：

- 统一判定“这对格式当前是否可达、代价多少、被哪条 guard 拦住”；
- 为 CLI ``--plan`` 与 GUI 提示提供可解释结果；
- 为未来的受限边（插件只允许特定中转、图片只允许图片族）保留多跳执行能力。

多跳执行 ``execute_plan`` 仍然可用：它只在调用方显式传入受限边图时才会产生
多于一步的计划；默认直连计划永远是一步。
"""

from __future__ import annotations

import heapq
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .catalog import FORMAT_FAMILIES, format_status, known_formats, normalize_assume_installed

_IMAGE_FORMATS: frozenset[str] = frozenset(FORMAT_FAMILIES["image"])
_SELF_EDGE_FORMATS: frozenset[str] = frozenset({".jsonl", *_IMAGE_FORMATS})
_HEAVY_FORMATS: frozenset[str] = frozenset({".pdf", ".parquet", ".duckdb", ".docx", ".xlsx"})
_COST_HEAVY = 15
_COST_NORMAL = 10
_COST_LIGHT = 8
_HOP_PENALTY = 5
_DEFAULT_MAX_STEPS = 3


@dataclass(frozen=True, slots=True)
class ConversionEdge:
    source: str
    target: str
    cost: int = _COST_NORMAL
    guard: str = ""


@dataclass(frozen=True, slots=True)
class PlanStep:
    source: str
    target: str
    cost: int
    guard: str = ""


@dataclass(frozen=True, slots=True)
class ConversionPlan:
    steps: tuple[PlanStep, ...]
    total_cost: int
    edges: tuple[ConversionEdge, ...] = field(default=(), repr=False)

    @property
    def source(self) -> str:
        return self.steps[0].source if self.steps else ""

    @property
    def target(self) -> str:
        return self.steps[-1].target if self.steps else ""

    @property
    def hops(self) -> int:
        return len(self.steps)

    def formats(self) -> tuple[str, ...]:
        if not self.steps:
            return ()
        return (self.steps[0].source, *(step.target for step in self.steps))

    def describe(self) -> str:
        parts = " → ".join(self.formats())
        return f"{parts}（{self.hops} 步，代价 {self.total_cost}）"


def _route_allowed(source: str, target: str) -> bool:
    return (source in _IMAGE_FORMATS) == (target in _IMAGE_FORMATS)


def _self_edge_allowed(fmt: str) -> bool:
    return fmt in _SELF_EDGE_FORMATS


def _edge_cost(source: str, target: str) -> int:
    if source in _IMAGE_FORMATS or target in _IMAGE_FORMATS:
        return _COST_NORMAL
    if source in {".csv", ".jsonl"} and target in {".csv", ".jsonl"}:
        return _COST_LIGHT
    if source in _HEAVY_FORMATS or target in _HEAVY_FORMATS:
        return _COST_HEAVY
    return _COST_NORMAL


def build_default_edges(*, assume_installed: list[str] | tuple[str, ...] | set[str] = ()) -> tuple[ConversionEdge, ...]:
    """按当前注册表 + guard 构造直连边图（格式级）。"""
    assumed = normalize_assume_installed(assume_installed)
    readable: list[str] = []
    writable: list[str] = []
    for fmt in known_formats():
        status = format_status(fmt, assume_installed=assumed)
        if status.readable:
            readable.append(fmt)
        if status.writable:
            writable.append(fmt)
    edges: list[ConversionEdge] = []
    for source in readable:
        for target in writable:
            if not _route_allowed(source, target):
                continue
            if source == target and not _self_edge_allowed(source):
                continue
            guard = "" if source != target else "reformat"
            if source in _IMAGE_FORMATS:
                guard = "image-only"
            edges.append(ConversionEdge(source, target, _edge_cost(source, target), guard))
    return tuple(edges)


def plan_conversion(
    source: str,
    target: str,
    *,
    edges: tuple[ConversionEdge, ...] | list[ConversionEdge] | None = None,
    max_steps: int = _DEFAULT_MAX_STEPS,
    assume_installed: list[str] | tuple[str, ...] | set[str] = (),
) -> ConversionPlan | None:
    """Dijkstra 求最小代价路径；不可达返回 None。

    ``max_steps`` 限制边数（默认 3）；每一步在边代价外追加 ``_HOP_PENALTY`` 的
    中转成本，因此只要直连存在就一定优先直连。
    """
    if max_steps < 1:
        return None
    source = source.lower() if source.startswith(".") else f".{source.lower()}"
    target = target.lower() if target.startswith(".") else f".{target.lower()}"
    edge_list = tuple(edges) if edges is not None else build_default_edges(assume_installed=assume_installed)
    adjacency: dict[str, list[ConversionEdge]] = {}
    for edge in edge_list:
        adjacency.setdefault(edge.source, []).append(edge)

    # (cost, hops, node)；用 hops 做第二排序键，保证同代价时步数更少者优先。
    heap: list[tuple[int, int, str]] = [(0, 0, source)]
    best: dict[tuple[str, int], int] = {(source, 0): 0}
    previous: dict[tuple[str, int], tuple[tuple[str, int], ConversionEdge]] = {}

    while heap:
        cost, hops, node = heapq.heappop(heap)
        if best.get((node, hops), cost + 1) != cost:
            continue
        if node == target and hops > 0:
            steps: list[PlanStep] = []
            cursor = (node, hops)
            while cursor in previous:
                prior, edge = previous[cursor]
                steps.append(PlanStep(edge.source, edge.target, edge.cost, edge.guard))
                cursor = prior
            steps.reverse()
            return ConversionPlan(tuple(steps), cost, tuple(edge_list))
        if hops >= max_steps:
            continue
        for edge in adjacency.get(node, ()):
            next_cost = cost + edge.cost + (_HOP_PENALTY if hops > 0 else 0)
            key = (edge.target, hops + 1)
            if next_cost >= best.get(key, 1 << 30):
                continue
            best[key] = next_cost
            previous[key] = ((node, hops), edge)
            heapq.heappush(heap, (next_cost, hops + 1, edge.target))
    return None


def reachable_formats(
    source: str,
    *,
    edges: tuple[ConversionEdge, ...] | list[ConversionEdge] | None = None,
    max_steps: int = _DEFAULT_MAX_STEPS,
) -> tuple[str, ...]:
    """从 source 出发在步数限制内可达的目标格式（稳定顺序）。"""
    edge_list = tuple(edges) if edges is not None else build_default_edges()
    targets: list[str] = []
    for candidate in known_formats():
        if candidate == source:
            continue
        if plan_conversion(source, candidate, edges=edge_list, max_steps=max_steps) is not None:
            targets.append(candidate)
    return tuple(targets)


def describe_plan(plan: ConversionPlan | None) -> str:
    if plan is None:
        return "不可达（当前依赖与 guard 下没有可用路径）"
    lines = [f"转换计划：{plan.describe()}"]
    for index, step in enumerate(plan.steps, start=1):
        guard = f"  [{step.guard}]" if step.guard else ""
        lines.append(f"  {index}. {step.source} → {step.target}  代价 {step.cost}{guard}")
    return "\n".join(lines)


def execute_plan(
    plan: ConversionPlan,
    source: Path,
    target: Path,
    *,
    options: dict[str, Any] | None = None,
    should_stop: Any = None,
) -> Any:
    """按计划逐步落盘并返回最后一步的 ``ConvertResult``。

    中间文件放在系统临时目录，``finally`` 自动清理。默认图下计划恒为一步，
    此函数主要用于受限边/插件扩展与测试。
    """
    from ._core import convert

    if not plan.steps:
        raise ValueError("ConvertX: 空转换计划")
    options = dict(options or {})
    warnings: list[str] = []
    with tempfile.TemporaryDirectory(prefix="convertx-plan-") as raw_temp:
        temp_dir = Path(raw_temp)
        current = source
        result: Any = None
        for index, step in enumerate(plan.steps):
            is_last = index == len(plan.steps) - 1
            destination = target if is_last else temp_dir / f"step-{index}{step.target}"
            result = convert(
                current,
                destination,
                src_format=step.source,
                dst_format=step.target,
                options=options,
                should_stop=should_stop,
            )
            warnings.extend(result.warnings)
            current = destination
    result.warnings[:0] = [warning for warning in warnings if warning not in result.warnings]
    result.extra["plan"] = [
        {"source": step.source, "target": step.target, "cost": step.cost, "guard": step.guard}
        for step in plan.steps
    ]
    return result


__all__ = [
    "ConversionEdge",
    "ConversionPlan",
    "PlanStep",
    "build_default_edges",
    "describe_plan",
    "execute_plan",
    "plan_conversion",
    "reachable_formats",
]
