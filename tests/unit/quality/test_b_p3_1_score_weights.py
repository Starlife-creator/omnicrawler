"""B-P3-1：质量评分权重常量化 + min_confidence 阈值加载期一次性解析校验。

覆盖：
- 0.4 完整性 / 0.6 必填满足率 / 0.1 每错误扣分（上限 0.5）公式可复算；
- ``min_confidence`` 越界在 ``assess_record`` 与 ``assess_records`` 入口均抛 ValueError；
- 合法阈值通过且不阻断评估。
"""

from __future__ import annotations

import pytest

from omnicrawler.core.models import ExtractedRecord
from omnicrawler.quality.quality import assess_record, assess_records


def _record(data: dict[str, object]) -> ExtractedRecord:
    return ExtractedRecord("https://example.org/1", "item", data)


def test_score_formula_matches_named_weights() -> None:
    # 两个必填字段各填一半：completeness=0.5、required_score=0.5、无错误
    # score = 0.5*0.4 + 0.5*0.6 = 0.5
    fields = {"a": {"required": True}, "b": {"required": True}}
    quality = assess_record(_record({"a": 1}), fields)
    assert quality["completeness"] == 0.5
    assert quality["score"] == pytest.approx(0.5)


def test_full_present_record_scores_one() -> None:
    fields = {"a": {"required": True}, "b": {"required": True}}
    quality = assess_record(_record({"a": 1, "b": 2}), fields)
    assert quality["score"] == pytest.approx(1.0)


def test_error_penalty_per_error_capped() -> None:
    # 一个 pattern 错误：score = 1.0 - 0.1 = 0.9
    fields = {"title": {"pattern": r"^\d+$"}}
    quality = assess_record(_record({"title": "abc"}), fields)
    assert quality["score"] == pytest.approx(0.9)


def test_min_confidence_out_of_range_rejected_single() -> None:
    fields = {"price": {"min_confidence": 1.5}}
    with pytest.raises(ValueError, match="min_confidence 必须在0到1之间"):
        assess_record(_record({"price": "1"}), fields)


def test_min_confidence_out_of_range_rejected_batch() -> None:
    fields = {"price": {"min_confidence": -0.2}}
    records = [_record({"price": "1"})]
    with pytest.raises(ValueError, match="min_confidence 必须在0到1之间"):
        assess_records(records, fields)


def test_valid_min_confidence_passes_through() -> None:
    fields = {"price": {"min_confidence": 0.5}}
    quality = assess_record(_record({"price": "1"}), fields)
    # 无证据轨迹时阈值不触发报错，评估正常返回
    assert quality["score"] is not None
