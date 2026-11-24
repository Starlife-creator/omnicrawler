"""`_tier_reason` 的审计文案：不许打印裸 float，也不许说假话。

为什么要有这个文件：原实现直接插值 float ——

```
置信度 0.9500000000000001 ≥ 阈值，质量 0.5→0.75
置信度 0.3333333333333333 ≥ 阈值，质量 0.5→0.75
```

第二行更糟：`0.33` 小于默认阈值 `0.85`，「≥ 阈值」是**假话**——
而且读者不知道阈值是多少，无法自己核对。

现在：置信度与质量都用百分比渲染，且**显式带上实际阈值**。
"""

from __future__ import annotations

import pytest

from omnicrawler.quality.auto_apply import (
    AutoApplyPolicy,
    AutomationTier,
    RepairCandidate,
    ShadowComparison,
    _tier_reason,
)


def _candidate(confidence: float) -> RepairCandidate:
    return RepairCandidate(
        candidate_id="c1",
        field="price",
        rule_type="css",
        old_rule=".a",
        new_rule=".b",
        confidence=confidence,
        supporting_samples=10,
        counterexamples=0,
        expected_recovery=0.9,
        false_positive_risk=0.01,
        observation_rounds=3,
        origin="rule",
    )


def _comparison() -> ShadowComparison:
    return ShadowComparison(
        old_records=100,
        new_records=100,
        old_quality=0.5,
        new_quality=0.75,
        false_matches=0,
        historical_compatible=True,
        baseline_sha256="a",
        candidate_sha256="b",
        evidence_sha256="c",
    )


def _l2_reason(confidence: float, threshold: float = 0.85) -> str:
    policy = AutoApplyPolicy(l2_confidence_threshold=threshold)
    return _tier_reason(AutomationTier.L2, _candidate(confidence), _comparison(), policy)


@pytest.mark.parametrize("confidence", [0.85, 0.9, 0.9500000000000001, 1 / 3, 0.9999999])
def test_no_long_float_noise(confidence: float) -> None:
    """十六位小数不得出现在审计文案里。"""
    text = _l2_reason(confidence)
    assert "00000" not in text, text
    assert "33333" not in text, text


def test_threshold_is_shown_so_readers_can_verify() -> None:
    """读者要能自己核对「≥ 阈值」是否成立，所以阈值必须写出来。"""
    text = _l2_reason(0.9, threshold=0.85)
    assert "85%" in text, text


def test_custom_threshold_is_reflected() -> None:
    """阈值可配置，文案要跟着变。"""
    assert "70%" in _l2_reason(0.8, threshold=0.7)
    assert "95%" in _l2_reason(0.99, threshold=0.95)


def test_quality_change_uses_percentage() -> None:
    """质量 0.5→0.75 应显示成 50.0%→75.0%（人读的是百分比）。"""
    text = _l2_reason(0.9)
    assert "50.0%" in text and "75.0%" in text, text


def test_l1_reason_also_has_no_bare_floats() -> None:
    """L1 分支同族问题，一并修。"""
    policy = AutoApplyPolicy()
    text = _tier_reason(AutomationTier.L1, _candidate(0.9), _comparison(), policy)
    assert "50.0%" in text and "75.0%" in text, text


def test_l0_and_l3_paths_are_unchanged() -> None:
    """只改 L1/L2 的文案，其它分支不动。"""
    policy = AutoApplyPolicy()
    assert "需人工批准" in _tier_reason(AutomationTier.L0, _candidate(0.5), _comparison(), policy)
    assert "稳定" in _tier_reason(AutomationTier.L3, _candidate(0.99), _comparison(), policy)
