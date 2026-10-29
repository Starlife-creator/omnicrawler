"""C-#16：``pipeline_ops.pipeline_stages`` 阶段顺序校验与上下文写入直接单测。"""
from __future__ import annotations

import pytest

from omnicrawler.pipeline_ops.pipeline_stages import (
    PIPELINE_STAGE_ORDER,
    FunctionStage,
    StageContext,
    require_stage_order,
)


def test_stage_order_is_stable_sequence() -> None:
    assert PIPELINE_STAGE_ORDER == (
        "plan", "policy", "fetch", "archive", "parse",
        "filter", "attachments_pdf", "quality", "export",
    )


def test_function_stage_runs_handler_and_records_result() -> None:
    def handler(context: StageContext) -> dict[str, object]:
        return {"run_id": context.run_id, "plan_hash": context.plan_hash}

    stage = FunctionStage("fetch", handler)
    context = StageContext(run_id="r1", plan_hash="h1", values={})
    result = stage.execute(context)
    assert result == {"run_id": "r1", "plan_hash": "h1"}
    assert context.values["fetch"] == result


def test_function_stage_rejects_unknown_stage_name() -> None:
    stage = FunctionStage("bogus", lambda context: {})
    with pytest.raises(ValueError, match="未知管线阶段"):
        stage.execute(StageContext("r1", "h1", {}))


def test_require_stage_order_accepts_canonical_subsequence() -> None:
    require_stage_order(["plan", "fetch", "export"])


def test_require_stage_order_rejects_out_of_order_and_duplicates() -> None:
    with pytest.raises(ValueError, match="标准顺序"):
        require_stage_order(["export", "fetch"])
    with pytest.raises(ValueError, match="标准顺序"):
        require_stage_order(["fetch", "fetch"])
