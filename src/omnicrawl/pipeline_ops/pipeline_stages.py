from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

PIPELINE_STAGE_ORDER = (
    "plan", "policy", "fetch", "archive", "parse", "filter", "attachments_pdf", "quality", "export"
)


@dataclass(slots=True)
class StageContext:
    run_id: str
    plan_hash: str
    values: dict[str, Any]


class PipelineStage(Protocol):
    name: str

    def execute(self, context: StageContext) -> dict[str, Any]: ...


@dataclass(slots=True)
class FunctionStage:
    name: str
    handler: Callable[[StageContext], dict[str, Any]]

    def execute(self, context: StageContext) -> dict[str, Any]:
        if self.name not in PIPELINE_STAGE_ORDER:
            raise ValueError(f"未知管线阶段: {self.name}")
        result = self.handler(context)
        context.values[self.name] = result
        return result


def require_stage_order(names: list[str] | tuple[str, ...]) -> None:
    positions = [PIPELINE_STAGE_ORDER.index(name) for name in names]
    if positions != sorted(positions) or len(positions) != len(set(positions)):
        raise ValueError("管线阶段必须按标准顺序且不能重复")
