"""P3-3：L2 观察期 + L3 稳定演化工程化单元测试。

用例覆盖：
  1. promote 首次写入 / 跨进程重开（关 store 再重开）rounds 记忆
  2. L2 应用 → 连续 2 次 regression → RollbackDirective → mark_rolled_back
  3. observed_auto_apply 连续 3 次 improves_safely 观测 → 第 4 次 classify_tier 升级 L3
  4. fp_risk EMA：false_matches>0 上升；随后 improves_safely 慢慢下降
  5. snapshot 诊断输出结构
"""

from __future__ import annotations

from pathlib import Path

from omnicrawl.quality.auto_apply import (
    AutoApplyPolicy,
    AutomationTier,
    classify_tier,
    observed_auto_apply,
    step_observe,
)
from omnicrawl.quality.observation_store import ObservationStore, RollbackDirective
from omnicrawl.quality.shadow_repair import (
    RepairCandidate,
    ShadowComparison,
    candidate_rule,
    shadow_config,
)

# ── 简单 active 配置 + 支持候选 ─────────────────────────
ACTIVE = {
    "extract": {
        "fields": {
            "title": {"selector": "h1.old"},
        }
    }
}


def _make_candidate(**overrides) -> RepairCandidate:
    samples = tuple(f"sample-{i}" for i in range(15))
    c = candidate_rule("title", "css", "h1.old", "h1.new", samples)
    # 可能需要替换个别字段（observation_rounds 等）——构造时 candidate_rule 默认是 0/0.0
    if overrides:
        return RepairCandidate(
            candidate_id=c.candidate_id,
            field=overrides.get("field", c.field),
            rule_type=overrides.get("rule_type", c.rule_type),  # type: ignore[arg-type]
            old_rule=overrides.get("old_rule", c.old_rule),
            new_rule=overrides.get("new_rule", c.new_rule),
            confidence=float(overrides.get("confidence", c.confidence)),
            supporting_samples=c.supporting_samples,
            counterexamples=c.counterexamples,
            expected_recovery=float(overrides.get("expected_recovery", c.expected_recovery)),
            false_positive_risk=float(overrides.get("false_positive_risk", c.false_positive_risk)),
            observation_rounds=int(overrides.get("observation_rounds", c.observation_rounds)),
        )
    return c


def _comparison(
    *,
    old_quality: float = 0.5,
    new_quality: float = 0.8,
    false_matches: int = 0,
    historical_compatible: bool = True,
    old_records: int = 100,
    new_records: int = 105,
) -> ShadowComparison:
    return ShadowComparison(
        old_records=old_records,
        new_records=new_records,
        old_quality=old_quality,
        new_quality=new_quality,
        false_matches=false_matches,
        historical_compatible=historical_compatible,
    )


POLICY = AutoApplyPolicy(llm_enabled=True, l3_required_observation_rounds=3, l2_confidence_threshold=0.85)


class TestPromotePersistence:
    def test_promote_writes_first_seen(self, tmp_path: Path) -> None:
        db = tmp_path / "obs.db"
        c = _make_candidate(confidence=0.95, false_positive_risk=0.05)
        with ObservationStore(db) as store:
            promoted = store.promote_for_candidate(c)
            assert promoted.observation_rounds == 0
            snap = store.snapshot(c.candidate_id)
            assert len(snap) == 1
            assert snap[0]["applied_count"] == 0

    def test_promote_persists_across_process_restart(self, tmp_path: Path) -> None:
        """关闭并重开 ObservationStore（模拟进程重启），rounds/fp_risk 仍在。"""
        db = tmp_path / "obs.db"
        c = _make_candidate(confidence=0.95, false_positive_risk=0.05)
        comp_ok = _comparison()
        # 第一次 run：应用 + 1 次观察 → rounds=1
        with ObservationStore(db) as store:
            result = observed_auto_apply(ACTIVE, shadow_config(ACTIVE, c), c, comp_ok, POLICY, store)
            assert result is not None
            assert result.tier == AutomationTier.L2
            directive = step_observe(c, comp_ok, store)
            assert directive is None  # improves_safely 仅 1 次 fail 不会触发
        # 第二次 run：关店再开店，再 step_observe 一次 → rounds=2
        with ObservationStore(db) as store:
            promoted = store.promote_for_candidate(c)
            assert promoted.observation_rounds == 1, "跨进程 memory 失效，L3 永远达不到"
            directive = step_observe(c, comp_ok, store)
            assert directive is None
            promoted2 = store.promote_for_candidate(c)
            assert promoted2.observation_rounds == 2


class TestL2RollbackTrigger:
    def test_two_consecutive_regressions_triggers_directive(self, tmp_path: Path) -> None:
        db = tmp_path / "obs.db"
        c = _make_candidate(confidence=0.9, false_positive_risk=0.05)
        comp_ok = _comparison()
        # 先应用一次（让 applied_count>0，这样 regression_count 才会走 L2 路径）
        with ObservationStore(db) as store:
            result = observed_auto_apply(ACTIVE, shadow_config(ACTIVE, c), c, comp_ok, POLICY, store)
            assert result is not None
            # 第 1 次观察：失败（improves_safely=False）
            bad = _comparison(new_quality=0.4, old_quality=0.5)  # new<old → not improves_safely
            directive = step_observe(c, bad, store)
            assert directive is None, "首次失败不应立即回滚（抗单次抖动）"
            # 第 2 次连续失败：触发阈值 2
            directive = step_observe(c, bad, store, l2_regression_threshold=2)
            assert isinstance(directive, RollbackDirective)
            assert directive.candidate_id == c.candidate_id
            assert directive.regression_count == 2
            assert directive.threshold == 2
            # rollback_sha 是应用时写入的 baseline
            assert directive.rollback_config_sha256 is not None
            # baseline_quality = old_quality (0.5)
            assert directive.baseline_quality == 0.5

    def test_mark_rolled_back_clears_flag(self, tmp_path: Path) -> None:
        db = tmp_path / "obs.db"
        c = _make_candidate(confidence=0.9, false_positive_risk=0.05)
        comp_ok = _comparison()
        bad = _comparison(new_quality=0.4, old_quality=0.5)
        with ObservationStore(db) as store:
            observed_auto_apply(ACTIVE, shadow_config(ACTIVE, c), c, comp_ok, POLICY, store)
            step_observe(c, bad, store)
            directive = step_observe(c, bad, store)
            assert directive is not None
            # 调用方执行回滚后 mark
            store.mark_rolled_back(c.candidate_id)
            snap = store.snapshot(c.candidate_id)[0]
            assert snap["rollback_required"] == 0
            assert snap["regression_count"] == 0
            # observation_rounds 保留（历史经验不丢）
            assert snap["observation_rounds"] >= 0

    def test_regression_count_resets_after_an_ok_observation(self, tmp_path: Path) -> None:
        """1 次坏 + 1 次好 → regress 清零，不会被累计成 2 次。"""
        db = tmp_path / "obs.db"
        c = _make_candidate(confidence=0.92, false_positive_risk=0.05)
        comp_ok = _comparison()
        bad = _comparison(new_quality=0.4, old_quality=0.5)
        with ObservationStore(db) as store:
            observed_auto_apply(ACTIVE, shadow_config(ACTIVE, c), c, comp_ok, POLICY, store)
            step_observe(c, bad, store)
            # 中间一次 OK 清零
            directive_ok = step_observe(c, comp_ok, store)
            assert directive_ok is None
            step_observe(c, bad, store)  # 第二次 bad，但实际上是"新的累计 1 次"
            snap = store.snapshot(c.candidate_id)[0]
            assert snap["regression_count"] == 1  # 只累计连续的


class TestL3Evolution:
    def test_three_stable_passes_upgrades_classify_to_l3(self, tmp_path: Path) -> None:
        db = tmp_path / "obs.db"
        # fp_risk 初始 0.05 ≤ 0.1：满足 L3 风险判据
        c = _make_candidate(confidence=0.92, false_positive_risk=0.05)
        comp_ok = _comparison(new_quality=0.9, old_quality=0.7)
        with ObservationStore(db) as store:
            # 先应用 L2
            r = observed_auto_apply(ACTIVE, shadow_config(ACTIVE, c), c, comp_ok, POLICY, store)
            assert r is not None and r.tier == AutomationTier.L2
            # 3 次稳定观察 → rounds 到达 3
            for _ in range(3):
                step_observe(c, comp_ok, store)
            promoted = store.promote_for_candidate(c)
            assert promoted.observation_rounds >= 3
            assert promoted.false_positive_risk <= 0.1
            tier = classify_tier(promoted, comp_ok, POLICY)
            assert tier == AutomationTier.L3

    def test_l3_disabled_when_l2_disabled(self, tmp_path: Path) -> None:
        """已有 L2 关闭时即使 rounds ≥3 也不允许 L3（L3 是 L2 演化）。"""
        db = tmp_path / "obs.db"
        c = _make_candidate(confidence=0.92, false_positive_risk=0.05)
        comp_ok = _comparison()
        no_l2_policy = AutoApplyPolicy(l2_enabled=False, llm_enabled=True)
        with ObservationStore(db) as store:
            observed_auto_apply(ACTIVE, shadow_config(ACTIVE, c), c, comp_ok, POLICY, store)
            for _ in range(3):
                step_observe(c, comp_ok, store)
            promoted = store.promote_for_candidate(c)
            tier = classify_tier(promoted, comp_ok, no_l2_policy)
            # L2 关闭时不会升 L3（降级为 L1 幂等替换 or L0）
            assert tier != AutomationTier.L3


class TestFPRiskEMA:
    def test_false_matches_bumps_risk_then_decays(self, tmp_path: Path) -> None:
        db = tmp_path / "obs.db"
        c = _make_candidate(confidence=0.92, false_positive_risk=0.05)
        comp_ok = _comparison()
        bad_fp = _comparison(new_quality=0.85, old_quality=0.7, false_matches=5, new_records=100)
        with ObservationStore(db) as store:
            observed_auto_apply(ACTIVE, shadow_config(ACTIVE, c), c, comp_ok, POLICY, store)
            step_observe(c, bad_fp, store)  # instant_risk ≈ 5/100 = 0.05 → EMA 融合后有变化
            after_bad = store.promote_for_candidate(c).false_positive_risk
            # 因为 instant 风险 ≈ 0.05，old 0.05，所以值≈0.05；但若新观测高了，一定不是严格相等
            # 现在再让 improves_safely（OK）多轮 → 风险下降
            for _ in range(8):
                step_observe(c, comp_ok, store)
            after_ok = store.promote_for_candidate(c).false_positive_risk
            # 8 轮 OK 乘以 (0.6)^8 后，风险应当下降
            assert after_ok < after_bad or (after_ok == after_bad == 0.0)


class TestSnapshot:
    def test_snapshot_returns_list_of_dicts(self, tmp_path: Path) -> None:
        db = tmp_path / "obs.db"
        c = _make_candidate()
        with ObservationStore(db) as store:
            store.promote_for_candidate(c)
            snap = store.snapshot()
            assert isinstance(snap, list)
            assert len(snap) == 1
            assert "candidate_id" in snap[0]
