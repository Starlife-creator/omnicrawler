"""复核决策与反馈样本的共用数据模型（叶子模块）。

`review_persistence.ReviewStore` 要落这两类对象，而 `review_workbench` /
`review_feedback` 又要用 `ReviewStore` 持久化——类型定义留在原模块会形成
`review_*` 三模块导入环（`tools/check_architecture.py` 的单调复杂度预算会判红）。
把纯数据结构下沉到本叶子模块，依赖方向变成单向：models ← persistence / workbench / feedback。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True, slots=True)
class ReviewDecision:
    record_id: str
    field: str
    old_value: Any
    new_value: Any
    scope: Literal["record", "similar", "rule_suggestion", "regression", "reprocess"]
    reviewer: str


@dataclass(frozen=True, slots=True)
class FeedbackSample:
    sample_id: str
    input_evidence: str
    predicted: Any
    corrected: Any
    rule_or_model: str
    risk: float
    information_gain: float
    approved_for_regression: bool
