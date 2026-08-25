"""分级自动化引擎 L0-L3 单元测试。

覆盖:
    - is_reversible 可逆性判断
    - classify_tier 各等级分级决策
    - auto_apply_if_safe 自动应用主流程
    - should_rollback_on_quality_drop 观察期回滚
    - 安全兜底（不可逆操作永远 L0、未安全改善永远 L0）
    - 策略配置开关（l1_enabled/l2_enabled/llm_enabled）
"""

from __future__ import annotations

import copy

from omnicrawler.quality.auto_apply import (
    AutoApplyPolicy,
    AutoApplyResult,
    AutomationTier,
    auto_apply_if_safe,
    classify_tier,
    is_reversible,
    should_rollback_on_quality_drop,
)
from omnicrawler.quality.shadow_repair import (
    RepairCandidate,
    ShadowComparison,
    candidate_rule,
    shadow_config,
)

# ── 测试夹具 ────────────────────────────────────────────────────────────

ACTIVE_CONFIG = {
    "source": {"seeds": ["https://example.com"]},
    "extract": {"fields": {"title": {"selector": "h1.old"}}},
}


def _safe_comparison(old_q: float = 0.7, new_q: float = 0.9) -> ShadowComparison:
    """质量安全改善的影子比较。"""
    return ShadowComparison(
        old_records=10,
        new_records=10,
        old_quality=old_q,
        new_quality=new_q,
        false_matches=0,
        historical_compatible=True,
    )


def _unsafe_comparison() -> ShadowComparison:
    """质量未安全改善的影子比较（有误匹配）。"""
    return ShadowComparison(10, 10, 0.7, 0.9, 1, True)


def _high_confidence_candidate(rule_type: str = "css") -> RepairCandidate:
    """高置信度修复候选（confidence ≥ 0.85）。

    confidence = n / (n+2) * (1 - risk)
    13 个支持样本 + 0 反例 → confidence = 13/15 * 1.0 ≈ 0.867 ≥ 0.85
    """
    return candidate_rule(
        field="title",
        rule_type=rule_type,
        old_rule="h1.old",
        new_rule="main h1",
        supporting=("s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s10", "s11", "s12", "s13"),
        counterexamples=(),
    )


def _stable_candidate(rule_type: str = "css") -> RepairCandidate:
    """已稳定修复候选（observation_rounds ≥ 3 + 误报风险 ≤ 0.1）。"""
    base = _high_confidence_candidate(rule_type)
    return RepairCandidate(
        candidate_id=base.candidate_id,
        field=base.field,
        rule_type=base.rule_type,
        old_rule=base.old_rule,
        new_rule=base.new_rule,
        confidence=base.confidence,
        supporting_samples=base.supporting_samples,
        counterexamples=base.counterexamples,
        expected_recovery=base.expected_recovery,
        false_positive_risk=0.05,
        observation_rounds=4,
    )


# ── is_reversible 测试 ─────────────────────────────────────────────────


class TestIsReversible:
    def test_css_xpath_jsonpath_are_reversible(self) -> None:
        assert is_reversible("css") is True
        assert is_reversible("xpath") is True
        assert is_reversible("jsonpath") is True

    def test_action_is_not_reversible(self) -> None:
        assert is_reversible("action") is False

    def test_unknown_type_is_not_reversible(self) -> None:
        assert is_reversible("unknown") is False


# ── classify_tier 测试 ─────────────────────────────────────────────────


class TestClassifyTier:
    def test_irreversible_action_always_l0(self) -> None:
        """不可逆操作（action）永远 L0，即使高置信且安全改善。"""
        candidate = _high_confidence_candidate("action")
        comparison = _safe_comparison()
        policy = AutoApplyPolicy(llm_enabled=True)
        assert classify_tier(candidate, comparison, policy) == AutomationTier.L0

    def test_unsafe_comparison_always_l0(self) -> None:
        """未通过安全改善检查（有误匹配）永远 L0。"""
        candidate = _high_confidence_candidate("css")
        comparison = _unsafe_comparison()
        policy = AutoApplyPolicy(llm_enabled=True)
        assert classify_tier(candidate, comparison, policy) == AutomationTier.L0

    def test_stable_candidate_classified_l3(self) -> None:
        """已稳定候选（观察轮数 ≥3 + 误报风险 ≤0.1）→ L3。"""
        candidate = _stable_candidate("css")
        comparison = _safe_comparison()
        policy = AutoApplyPolicy(llm_enabled=True)
        assert classify_tier(candidate, comparison, policy) == AutomationTier.L3

    def test_high_confidence_llm_enabled_classified_l2(self) -> None:
        """高置信度 + LLM 可用 + 可逆 → L2。"""
        candidate = _high_confidence_candidate("css")
        comparison = _safe_comparison()
        policy = AutoApplyPolicy(llm_enabled=True)
        assert classify_tier(candidate, comparison, policy) == AutomationTier.L2

    def test_high_confidence_llm_disabled_falls_to_l1(self) -> None:
        """LLM 不可用时，高置信候选降级为 L1（幂等自动）。"""
        candidate = _high_confidence_candidate("css")
        comparison = _safe_comparison()
        policy = AutoApplyPolicy(llm_enabled=False)
        assert classify_tier(candidate, comparison, policy) == AutomationTier.L1

    def test_l2_disabled_falls_to_l1(self) -> None:
        """l2_enabled=False 时，高置信候选降级为 L1。"""
        candidate = _high_confidence_candidate("css")
        comparison = _safe_comparison()
        policy = AutoApplyPolicy(llm_enabled=True, l2_enabled=False)
        assert classify_tier(candidate, comparison, policy) == AutomationTier.L1

    def test_l1_disabled_falls_to_l0(self) -> None:
        """l1_enabled=False 且 LLM 不可用 → L0。"""
        candidate = _high_confidence_candidate("css")
        comparison = _safe_comparison()
        policy = AutoApplyPolicy(llm_enabled=False, l1_enabled=False)
        assert classify_tier(candidate, comparison, policy) == AutomationTier.L0

    def test_low_confidence_falls_to_l1(self) -> None:
        """低置信度（< 0.85）+ LLM 可用 → L1（幂等选择器仍可自动）。"""
        candidate = candidate_rule("title", "css", "h1.old", "main h1", ("s1", "s2"))
        comparison = _safe_comparison()
        policy = AutoApplyPolicy(llm_enabled=True)
        assert classify_tier(candidate, comparison, policy) == AutomationTier.L1

    def test_l3_takes_precedence_over_l2(self) -> None:
        """稳定候选优先 L3 而非 L2。"""
        candidate = _stable_candidate("css")
        comparison = _safe_comparison()
        policy = AutoApplyPolicy(llm_enabled=True)
        tier = classify_tier(candidate, comparison, policy)
        assert tier == AutomationTier.L3


# ── auto_apply_if_safe 测试 ────────────────────────────────────────────


class TestAutoApplyIfSafe:
    def test_l0_returns_none_for_irreversible(self) -> None:
        """不可逆操作返回 None，需人工批准。"""
        active = copy.deepcopy(ACTIVE_CONFIG)
        candidate = _high_confidence_candidate("action")
        shadow = shadow_config(active, candidate)
        comparison = _safe_comparison()
        policy = AutoApplyPolicy(llm_enabled=True)
        result = auto_apply_if_safe(active, shadow, candidate, comparison, policy)
        assert result is None

    def test_l0_returns_none_for_unsafe_comparison(self) -> None:
        """未安全改善返回 None。"""
        active = copy.deepcopy(ACTIVE_CONFIG)
        candidate = _high_confidence_candidate("css")
        shadow = shadow_config(active, candidate)
        comparison = _unsafe_comparison()
        policy = AutoApplyPolicy(llm_enabled=True)
        result = auto_apply_if_safe(active, shadow, candidate, comparison, policy)
        assert result is None

    def test_l1_applies_idempotent_repair(self) -> None:
        """L1 幂等修复自动应用，返回带审计标记的配置。"""
        active = copy.deepcopy(ACTIVE_CONFIG)
        candidate = _high_confidence_candidate("css")
        shadow = shadow_config(active, candidate)
        comparison = _safe_comparison()
        policy = AutoApplyPolicy(llm_enabled=False)  # 降级 L1
        result = auto_apply_if_safe(active, shadow, candidate, comparison, policy)
        assert result is not None
        assert result.tier == AutomationTier.L1
        assert result.config["_repair"]["approved_by"] == "auto:L1"
        assert result.rollback_available is True
        # active 未被修改
        assert active == ACTIVE_CONFIG

    def test_l2_applies_high_confidence_llm_repair(self) -> None:
        """L2 高置信 LLM 修复自动应用，进入观察期。"""
        active = copy.deepcopy(ACTIVE_CONFIG)
        candidate = _high_confidence_candidate("css")
        shadow = shadow_config(active, candidate)
        comparison = _safe_comparison()
        policy = AutoApplyPolicy(llm_enabled=True)
        result = auto_apply_if_safe(active, shadow, candidate, comparison, policy)
        assert result is not None
        assert result.tier == AutomationTier.L2
        assert result.config["_repair"]["approved_by"] == "auto:L2"
        # L2 未稳定 → observing 状态
        assert result.config["_repair"]["status"] == "observing"

    def test_l3_applies_stable_repair(self) -> None:
        """L3 已稳定修复自动应用，状态为 stable。"""
        active = copy.deepcopy(ACTIVE_CONFIG)
        candidate = _stable_candidate("css")
        shadow = shadow_config(active, candidate)
        comparison = _safe_comparison()
        policy = AutoApplyPolicy(llm_enabled=True)
        result = auto_apply_if_safe(active, shadow, candidate, comparison, policy)
        assert result is not None
        assert result.tier == AutomationTier.L3
        assert result.config["_repair"]["approved_by"] == "auto:L3"
        # L3 已稳定 → stable 状态
        assert result.config["_repair"]["status"] == "stable"

    def test_custom_actor_in_audit_log(self) -> None:
        """自定义 actor 标记写入审计日志。"""
        active = copy.deepcopy(ACTIVE_CONFIG)
        candidate = _high_confidence_candidate("css")
        shadow = shadow_config(active, candidate)
        comparison = _safe_comparison()
        policy = AutoApplyPolicy(llm_enabled=False)
        result = auto_apply_if_safe(
            active, shadow, candidate, comparison, policy, actor="pipeline-abc"
        )
        assert result is not None
        assert result.config["_repair"]["approved_by"] == "pipeline-abc:L1"

    def test_config_contains_rollback_snapshot(self) -> None:
        """自动应用的配置包含 rollback_config_sha256。"""
        active = copy.deepcopy(ACTIVE_CONFIG)
        candidate = _high_confidence_candidate("css")
        shadow = shadow_config(active, candidate)
        comparison = _safe_comparison()
        policy = AutoApplyPolicy(llm_enabled=True)
        result = auto_apply_if_safe(active, shadow, candidate, comparison, policy)
        assert result is not None
        assert result.config["_repair"]["rollback_config_sha256"]
        assert len(result.config["_repair"]["rollback_config_sha256"]) == 64

    def test_reason_is_human_readable(self) -> None:
        """应用原因包含人类可读信息。"""
        active = copy.deepcopy(ACTIVE_CONFIG)
        candidate = _high_confidence_candidate("css")
        shadow = shadow_config(active, candidate)
        comparison = _safe_comparison()
        policy = AutoApplyPolicy(llm_enabled=True)
        result = auto_apply_if_safe(active, shadow, candidate, comparison, policy)
        assert result is not None
        assert "置信度" in result.reason or "幂等" in result.reason


# ── should_rollback_on_quality_drop 测试 ───────────────────────────────


class TestShouldRollbackOnQualityDrop:
    def test_l2_rolls_back_when_quality_drops_below_baseline(self) -> None:
        """L2 观察期：质量降到基线以下 → 回滚。"""
        result = AutoApplyResult(
            tier=AutomationTier.L2,
            config={},
            candidate_id="test",
            reason="test",
            rollback_available=True,
        )
        assert should_rollback_on_quality_drop(result, 0.65, 0.7) is True

    def test_l2_does_not_rollback_when_quality_improves(self) -> None:
        """L2 观察期：质量持续改善 → 不回滚。"""
        result = AutoApplyResult(
            tier=AutomationTier.L2,
            config={},
            candidate_id="test",
            reason="test",
            rollback_available=True,
        )
        assert should_rollback_on_quality_drop(result, 0.85, 0.7) is False

    def test_l3_rolls_back_only_on_severe_drop(self) -> None:
        """L3 已稳定：仅质量大幅下降（低于基线 90%）才回滚。"""
        result = AutoApplyResult(
            tier=AutomationTier.L3,
            config={},
            candidate_id="test",
            reason="test",
            rollback_available=True,
        )
        # 0.68 > 0.7 * 0.9 = 0.63 → 不回滚
        assert should_rollback_on_quality_drop(result, 0.68, 0.7) is False
        # 0.60 < 0.63 → 回滚
        assert should_rollback_on_quality_drop(result, 0.60, 0.7) is True

    def test_l1_never_rolls_back(self) -> None:
        """L1 幂等修复不回滚（操作可重跑还原）。"""
        result = AutoApplyResult(
            tier=AutomationTier.L1,
            config={},
            candidate_id="test",
            reason="test",
            rollback_available=True,
        )
        assert should_rollback_on_quality_drop(result, 0.0, 0.9) is False


# ── 策略配置边界测试 ───────────────────────────────────────────────────


class TestPolicyEdgeCases:
    def test_custom_confidence_threshold(self) -> None:
        """自定义置信度阈值：0.95 时高置信候选不再满足 L2。"""
        candidate = _high_confidence_candidate("css")
        comparison = _safe_comparison()
        # 默认阈值 0.85 → L2
        policy_default = AutoApplyPolicy(llm_enabled=True)
        assert classify_tier(candidate, comparison, policy_default) == AutomationTier.L2
        # 阈值提到 0.99 → 降级 L1
        policy_strict = AutoApplyPolicy(llm_enabled=True, l2_confidence_threshold=0.99)
        assert classify_tier(candidate, comparison, policy_strict) == AutomationTier.L1

    def test_custom_l3_observation_rounds(self) -> None:
        """自定义 L3 所需观察轮数。"""
        base = _high_confidence_candidate("css")
        # observation_rounds=2 + risk=0.05
        candidate = RepairCandidate(
            candidate_id=base.candidate_id,
            field=base.field,
            rule_type=base.rule_type,
            old_rule=base.old_rule,
            new_rule=base.new_rule,
            confidence=base.confidence,
            supporting_samples=base.supporting_samples,
            counterexamples=base.counterexamples,
            expected_recovery=base.expected_recovery,
            false_positive_risk=0.05,
            observation_rounds=2,
        )
        comparison = _safe_comparison()
        # 默认 3 轮 → 2 轮不满足 L3，降级 L2
        policy_default = AutoApplyPolicy(llm_enabled=True)
        assert classify_tier(candidate, comparison, policy_default) == AutomationTier.L2
        # 降到 2 轮 → 满足 L3
        policy_relaxed = AutoApplyPolicy(
            llm_enabled=True, l3_required_observation_rounds=2
        )
        assert (
            classify_tier(candidate, comparison, policy_relaxed)
            == AutomationTier.L3
        )

    def test_all_disabled_falls_to_l0(self) -> None:
        """所有自动应用开关关闭 → L0。"""
        candidate = _high_confidence_candidate("css")
        comparison = _safe_comparison()
        policy = AutoApplyPolicy(
            llm_enabled=False, l1_enabled=False, l2_enabled=False
        )
        assert classify_tier(candidate, comparison, policy) == AutomationTier.L0


# ── 集成场景测试 ───────────────────────────────────────────────────────


class TestIntegrationScenarios:
    def test_full_l2_workflow_with_rollback_check(self) -> None:
        """完整 L2 工作流：自动应用 → 观察期质量下降 → 触发回滚。"""
        active = copy.deepcopy(ACTIVE_CONFIG)
        candidate = _high_confidence_candidate("css")
        shadow = shadow_config(active, candidate)
        comparison = _safe_comparison(old_q=0.7, new_q=0.9)
        policy = AutoApplyPolicy(llm_enabled=True)

        # 自动应用
        result = auto_apply_if_safe(active, shadow, candidate, comparison, policy)
        assert result is not None
        assert result.tier == AutomationTier.L2

        # 观察期质量下降到基线以下 → 触发回滚
        should_rollback = should_rollback_on_quality_drop(
            result, current_quality=0.65, baseline_quality=0.7
        )
        assert should_rollback is True

    def test_l3_stable_survives_minor_quality_fluctuation(self) -> None:
        """L3 已稳定：小幅质量波动不触发回滚。"""
        active = copy.deepcopy(ACTIVE_CONFIG)
        candidate = _stable_candidate("css")
        shadow = shadow_config(active, candidate)
        comparison = _safe_comparison(old_q=0.7, new_q=0.9)
        policy = AutoApplyPolicy(llm_enabled=True)

        result = auto_apply_if_safe(active, shadow, candidate, comparison, policy)
        assert result is not None
        assert result.tier == AutomationTier.L3

        # 小幅波动（0.69 > 0.63）→ 不回滚
        should_rollback = should_rollback_on_quality_drop(
            result, current_quality=0.69, baseline_quality=0.7
        )
        assert should_rollback is False

    def test_conservative_policy_treats_everything_as_l0(self) -> None:
        """保守策略：所有开关关闭，一切需人工批准。"""
        active = copy.deepcopy(ACTIVE_CONFIG)
        candidate = _stable_candidate("css")
        shadow = shadow_config(active, candidate)
        comparison = _safe_comparison()
        policy = AutoApplyPolicy(
            llm_enabled=False, l1_enabled=False, l2_enabled=False
        )
        result = auto_apply_if_safe(active, shadow, candidate, comparison, policy)
        assert result is None

# ── FINAL-S6：LLM 候选禁走 L1 免审通道 ──────────────────────────────────


def test_llm_origin_candidate_never_gets_l1_free_pass() -> None:
    """origin=="llm" 的低置信候选必须降级 L0，不得走 L1 免审。

    攻击路径：攻击者构造页面使恶意选择器"本地验证命中"——验证数据本就
    来自该页面。若允许 LLM 候选进 L1（默认启用），即无人批准写入活跃配置。
    """
    from dataclasses import replace

    low_confidence = candidate_rule(
        field="title",
        rule_type="css",
        old_rule="h1.old",
        new_rule="main h1",
        supporting=(),
        counterexamples=(),
    )
    llm_low = replace(low_confidence, origin="llm")
    policy = AutoApplyPolicy(l1_enabled=True, l2_enabled=True, llm_enabled=True)
    comparison = _safe_comparison()

    assert classify_tier(low_confidence, comparison, policy) is AutomationTier.L1
    assert classify_tier(llm_low, comparison, policy) is AutomationTier.L0


def test_llm_origin_high_confidence_still_reaches_l2() -> None:
    """高置信 LLM 候选不受影响：仍按既有规则达 L2（观察期）。"""
    from dataclasses import replace

    base = _high_confidence_candidate()
    llm_high = replace(base, origin="llm")
    policy = AutoApplyPolicy(l1_enabled=True, l2_enabled=True, llm_enabled=True)

    assert (
        classify_tier(llm_high, _safe_comparison(), policy) is AutomationTier.L2
    )
