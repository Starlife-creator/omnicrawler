"""LLM 影子修复候选生成器单元测试。

覆盖:
    - proposal_to_candidate 转换逻辑
    - build_comparison_from_proposal 比较结果构造
    - LLMCandidateGenerator 端到端流程（mock LLM）
    - generate_and_auto_apply 与 auto_apply 联动
    - 安全边界（未验证提议不采纳、样本不足不采纳）
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

pytest.importorskip("lxml")

from omnicrawler.core.models import ExtractedRecord  # noqa: E402
from omnicrawler.extraction.adaptive_extractor import RepairProposal  # noqa: E402
from omnicrawler.quality.auto_apply import (  # noqa: E402
    AutoApplyPolicy,
    AutomationTier,
)
from omnicrawler.quality.llm_candidate_generator import (  # noqa: E402
    CandidateWithComparison,
    LLMCandidateGenerator,
    build_comparison_from_proposal,
    generate_and_auto_apply,
    proposal_to_candidate,
)
from omnicrawler.quality.shadow_repair import (  # noqa: E402
    RepairCandidate,
    ShadowComparison,
    candidate_rule,
)

# ── 测试夹具 ────────────────────────────────────────────────────────────


def _safe_comparison_for(_candidate: RepairCandidate) -> ShadowComparison:
    """质量安全改善的影子比较（配合 candidate_rule 构造的候选使用）。"""
    return ShadowComparison(10, 10, 0.7, 0.9, 0, True)

_HTML = """<html><body>
<div class="card">
  <h2 class="name">商品A</h2>
  <span class="price">99.9</span>
</div>
<div class="card">
  <h2 class="name">商品B</h2>
  <span class="price">199.0</span>
</div>
<div class="card">
  <h2 class="name">商品C</h2>
  <span class="price">299.0</span>
</div>
</body></html>"""

_FIELDS: dict[str, Any] = {
    "title": {"selector": ".old-title"},
    "price": {"selector": ".old-price"},
}

ACTIVE_CONFIG = {
    "source": {"seeds": ["https://example.com"]},
    "extract": {"fields": {"title": {"selector": ".old-title"}}},
}


def _record(data: dict[str, Any]) -> ExtractedRecord:
    return ExtractedRecord("https://example.com/item", "item", data)


def _failing_records() -> list[ExtractedRecord]:
    return [
        _record({"title": "", "price": None}),
        _record({"title": "", "price": ""}),
    ]


def _mock_llm(selector: str = ".name", rule_type: str = "css") -> Any:
    """Mock LLM 返回指定选择器。"""

    def generate(prompt: str) -> str:
        return json.dumps({"rule_type": rule_type, "selector": selector})

    return generate


def _verified_proposal(
    field: str = "title",
    rule_type: str = "css",
    old_rule: str = ".old-title",
    new_rule: str = ".name",
    matches: int = 3,
    sample_values: tuple[str, ...] = ("商品A", "商品B", "商品C"),
) -> RepairProposal:
    """构造已通过验证的 RepairProposal。"""
    return RepairProposal(
        field=field,
        rule_type=rule_type,
        old_rule=old_rule,
        new_rule=new_rule,
        matches=matches,
        sample_values=sample_values,
        generated_by="llm",
        verified=True,
    )


# ── proposal_to_candidate 测试 ─────────────────────────────────────────


class TestProposalToCandidate:
    def test_verified_proposal_converts_to_candidate(self) -> None:
        """已验证提议转换为候选，置信度由支持样本数计算。"""
        proposal = _verified_proposal(sample_values=("商品A", "商品B", "商品C"))
        candidate = proposal_to_candidate(proposal)
        assert candidate is not None
        assert candidate.field == "title"
        assert candidate.old_rule == ".old-title"
        assert candidate.new_rule == ".name"
        assert candidate.rule_type == "css"
        # 3 个支持样本 → confidence = 3/5 * 1.0 = 0.6
        assert candidate.confidence == 0.6
        assert candidate.observation_rounds == 0

    def test_unverified_proposal_returns_none(self) -> None:
        """未通过验证的提议返回 None。"""
        proposal = RepairProposal(
            field="title",
            rule_type="css",
            old_rule=".old",
            new_rule=".new",
            matches=0,
            sample_values=(),
            generated_by="llm",
            verified=False,
        )
        assert proposal_to_candidate(proposal) is None

    def test_insufficient_samples_returns_none(self) -> None:
        """样本不足（0 个）返回 None。"""
        proposal = _verified_proposal(sample_values=())
        assert proposal_to_candidate(proposal) is None

    def test_observation_rounds_propagated(self) -> None:
        """观察轮数正确传播。"""
        proposal = _verified_proposal()
        candidate = proposal_to_candidate(proposal, observation_rounds=4)
        assert candidate is not None
        assert candidate.observation_rounds == 4

    def test_single_sample_low_confidence(self) -> None:
        """单个样本置信度低：1/3 * 1.0 ≈ 0.333。"""
        proposal = _verified_proposal(sample_values=("only",))
        candidate = proposal_to_candidate(proposal)
        assert candidate is not None
        assert candidate.confidence < 0.5


# ── build_comparison_from_proposal 测试 ────────────────────────────────


class TestBuildComparisonFromProposal:
    def test_comparison_reflects_proposal_matches(self) -> None:
        """比较结果的 records 数反映提议命中数。"""
        proposal = _verified_proposal(matches=5)
        comparison = build_comparison_from_proposal(
            proposal, old_quality=0.3, new_quality=0.8
        )
        assert comparison.old_records == 5
        assert comparison.new_records == 5
        assert comparison.old_quality == 0.3
        assert comparison.new_quality == 0.8
        assert comparison.false_matches == 0
        assert comparison.historical_compatible is True

    def test_comparison_improves_safely_when_quality_rises(self) -> None:
        """质量上升 + 无误匹配 → improves_safely=True。"""
        proposal = _verified_proposal()
        comparison = build_comparison_from_proposal(
            proposal, old_quality=0.3, new_quality=0.9
        )
        assert comparison.improves_safely is True

    def test_comparison_not_safe_when_quality_drops(self) -> None:
        """质量下降 → improves_safely=False。"""
        proposal = _verified_proposal()
        comparison = build_comparison_from_proposal(
            proposal, old_quality=0.9, new_quality=0.3
        )
        assert comparison.improves_safely is False


# ── LLMCandidateGenerator 端到端测试 ───────────────────────────────────


class TestLLMCandidateGenerator:
    def test_generate_candidates_with_mock_llm(self) -> None:
        """Mock LLM 返回有效选择器 → 生成候选。"""
        generator = LLMCandidateGenerator(
            llm_generate=_mock_llm(".name"),
            success_threshold=0.7,
        )
        results = generator.generate_candidates(
            _HTML,
            _failing_records(),
            _FIELDS,
            old_quality=0.0,
            new_quality=0.9,
        )
        # title 字段失效 → LLM 生成 .name → 本地验证 3 命中
        assert len(results) >= 1
        item = results[0]
        assert isinstance(item, CandidateWithComparison)
        assert item.candidate.field == "title"
        assert item.candidate.new_rule == ".name"
        assert item.comparison.new_quality == 0.9
        assert item.comparison.improves_safely is True

    def test_comparisons_property_reflects_last_run(self) -> None:
        """comparisons 属性反映最近一次生成的比较结果。"""
        generator = LLMCandidateGenerator(llm_generate=_mock_llm(".name"))
        generator.generate_candidates(
            _HTML, _failing_records(), _FIELDS, old_quality=0.0, new_quality=0.9
        )
        comparisons = generator.comparisons
        assert len(comparisons) >= 1
        assert all(isinstance(c, ShadowComparison) for c in comparisons)

    def test_candidates_property_reflects_last_run(self) -> None:
        """candidates 属性反映最近一次生成的候选。"""
        generator = LLMCandidateGenerator(llm_generate=_mock_llm(".name"))
        generator.generate_candidates(
            _HTML, _failing_records(), _FIELDS, old_quality=0.0, new_quality=0.9
        )
        candidates = generator.candidates
        assert len(candidates) >= 1
        assert all(isinstance(c, RepairCandidate) for c in candidates)

    def test_no_failing_fields_returns_empty(self) -> None:
        """无失效字段 → 返回空列表。"""
        generator = LLMCandidateGenerator(llm_generate=_mock_llm(".name"))
        # 全部成功的记录
        records = [
            _record({"title": "ok", "price": "ok"}),
            _record({"title": "ok", "price": "ok"}),
        ]
        results = generator.generate_candidates(_HTML, records, _FIELDS)
        assert results == []

    def test_llm_returns_invalid_selector_returns_empty(self) -> None:
        """LLM 返回无效选择器（0 命中）→ 不产出候选。"""

        def bad_llm(prompt: str) -> str:
            return json.dumps({"rule_type": "css", "selector": ".nonexistent"})

        generator = LLMCandidateGenerator(llm_generate=bad_llm)
        results = generator.generate_candidates(
            _HTML, _failing_records(), _FIELDS
        )
        # .nonexistent 在 _HTML 中 0 命中 → 验证不通过
        assert results == []

    def test_llm_returns_invalid_json_returns_empty(self) -> None:
        """LLM 返回非 JSON → 不产出候选。"""

        def bad_llm(prompt: str) -> str:
            return "not a json"

        generator = LLMCandidateGenerator(llm_generate=bad_llm)
        results = generator.generate_candidates(
            _HTML, _failing_records(), _FIELDS
        )
        assert results == []

    def test_observation_rounds_propagated_to_candidate(self) -> None:
        """观察轮数传播到候选。"""
        generator = LLMCandidateGenerator(llm_generate=_mock_llm(".name"))
        results = generator.generate_candidates(
            _HTML,
            _failing_records(),
            _FIELDS,
            observation_rounds=3,
        )
        assert len(results) >= 1
        assert results[0].candidate.observation_rounds == 3


# ── generate_and_auto_apply 联动测试 ───────────────────────────────────


class TestGenerateAndAutoApply:
    def test_llm_origin_low_confidence_requires_manual_approval(self) -> None:
        """FINAL-S6：LLM 来源候选置信不足 L2 时降级 L0（人工批准），不走 L1。

        原行为是降级 L1 自动应用——但 LLM 候选的规则内容源自不可信页面，
        本地验证数据同样来自该页面，免审通道构成 prompt injection →
        配置持久化的最短路径。
        """
        generator = LLMCandidateGenerator(llm_generate=_mock_llm(".name"))
        candidates = generator.generate_candidates(
            _HTML,
            _failing_records(),
            _FIELDS,
            old_quality=0.0,
            new_quality=0.9,
        )
        assert len(candidates) >= 1
        # 转换器必须保留来源标记
        assert all(item.candidate.origin == "llm" for item in candidates)

        active = copy.deepcopy(ACTIVE_CONFIG)
        policy = AutoApplyPolicy(llm_enabled=False)
        results = generate_and_auto_apply(active, candidates, policy)
        assert len(results) == len(candidates)
        # 低置信 + LLM 来源 → 全部 L0 → None（等人工）
        assert all(r is None for r in results)

    def test_auto_applies_l2_when_high_confidence(self) -> None:
        """高置信度 + LLM 启用 → L2 自动应用（观察期）。"""
        from dataclasses import replace

        base = candidate_rule(
            field="title",
            rule_type="css",
            old_rule="h1.old",
            new_rule=".name",
            supporting=tuple(f"s{i}" for i in range(13)),
            counterexamples=(),
        )
        llm_high = replace(base, origin="llm")
        active = copy.deepcopy(ACTIVE_CONFIG)
        policy = AutoApplyPolicy(llm_enabled=True)
        results = generate_and_auto_apply(active, [CandidateWithComparison(llm_high, _safe_comparison_for(base))], policy)
        applied = [r for r in results if r is not None]
        assert len(applied) == 1
        assert applied[0].tier == AutomationTier.L2

    def test_conservative_policy_returns_all_none(self) -> None:
        """保守策略（全关闭）→ 全部 None，需人工。"""
        generator = LLMCandidateGenerator(llm_generate=_mock_llm(".name"))
        candidates = generator.generate_candidates(
            _HTML,
            _failing_records(),
            _FIELDS,
            old_quality=0.0,
            new_quality=0.9,
        )
        active = copy.deepcopy(ACTIVE_CONFIG)
        policy = AutoApplyPolicy(
            llm_enabled=False, l1_enabled=False, l2_enabled=False
        )
        results = generate_and_auto_apply(active, candidates, policy)
        assert all(r is None for r in results)

    def test_active_config_not_mutated(self) -> None:
        """自动应用不修改活跃配置。"""
        generator = LLMCandidateGenerator(llm_generate=_mock_llm(".name"))
        candidates = generator.generate_candidates(
            _HTML,
            _failing_records(),
            _FIELDS,
            old_quality=0.0,
            new_quality=0.9,
        )
        active = copy.deepcopy(ACTIVE_CONFIG)
        original = copy.deepcopy(active)
        policy = AutoApplyPolicy(llm_enabled=False)
        generate_and_auto_apply(active, candidates, policy)
        assert active == original

    def test_custom_actor_in_audit_log(self) -> None:
        """自定义 actor 写入审计日志（经 L2 通道验证）。"""
        from dataclasses import replace

        base = candidate_rule(
            field="title",
            rule_type="css",
            old_rule="h1.old",
            new_rule=".name",
            supporting=tuple(f"s{i}" for i in range(13)),
            counterexamples=(),
        )
        llm_high = replace(base, origin="llm")
        active = copy.deepcopy(ACTIVE_CONFIG)
        policy = AutoApplyPolicy(llm_enabled=True)
        results = generate_and_auto_apply(
            active,
            [CandidateWithComparison(llm_high, _safe_comparison_for(base))],
            policy,
            actor="pipeline-xyz",
        )
        applied = [r for r in results if r is not None]
        assert applied
        assert applied[0].config["_repair"]["approved_by"].startswith("pipeline-xyz:")

    def test_empty_candidates_returns_empty_results(self) -> None:
        """空候选列表 → 空结果列表。"""
        active = copy.deepcopy(ACTIVE_CONFIG)
        policy = AutoApplyPolicy(llm_enabled=True)
        results = generate_and_auto_apply(active, [], policy)
        assert results == []


# ── 集成场景测试 ───────────────────────────────────────────────────────


class TestIntegrationScenarios:
    def test_full_workflow_detect_generate_apply(self) -> None:
        """完整工作流：检测失效 → LLM 生成 → 本地验证 → 转候选。

        FINAL-S6：LLM 来源候选置信不足 L2 时不再自动应用（原走 L1 免审，
        现降级 L0 等人工批准），活跃配置保持不变。
        """
        # 1. 准备失效场景
        generator = LLMCandidateGenerator(
            llm_generate=_mock_llm(".name"),
            success_threshold=0.7,
        )

        # 2. 生成候选（带来源标记）
        candidates = generator.generate_candidates(
            _HTML,
            _failing_records(),
            _FIELDS,
            old_quality=0.0,
            new_quality=0.9,
        )
        assert len(candidates) >= 1
        assert all(item.candidate.origin == "llm" for item in candidates)

        # 3. 自动应用（低置信 LLM 候选 → L0 → 不应用）
        active = copy.deepcopy(ACTIVE_CONFIG)
        original = copy.deepcopy(active)
        policy = AutoApplyPolicy(llm_enabled=False)
        results = generate_and_auto_apply(active, candidates, policy)

        # 4. 验证：全部等人工、活跃配置未被改动
        assert len(results) == len(candidates)
        assert all(r is None for r in results)
        assert active == original

    def test_xpath_proposal_supported(self) -> None:
        """XPath 类型提议也支持转换。"""
        proposal = RepairProposal(
            field="content",
            rule_type="xpath",
            old_rule="//div[@class='old']",
            new_rule="//div[@class='new']",
            matches=2,
            sample_values=("text1", "text2"),
            generated_by="llm",
            verified=True,
        )
        candidate = proposal_to_candidate(proposal)
        assert candidate is not None
        assert candidate.rule_type == "xpath"
        assert is_reversible_xpath(candidate)

    def test_action_proposal_classified_l0(self) -> None:
        """action 类型提议 → L0（不可逆，需人工）。

        注：adaptive_extractor 当前只生成 css/xpath，这里直接构造
        RepairCandidate 测试 action 的 L0 分级。
        """
        from omnicrawler.quality.shadow_repair import candidate_rule

        candidate = candidate_rule(
            field="submit",
            rule_type="action",
            old_rule="click#old",
            new_rule="click#new",
            supporting=("s1", "s2", "s3"),
        )
        comparison = ShadowComparison(10, 10, 0.3, 0.9, 0, True)
        item = CandidateWithComparison(candidate, comparison)

        active = copy.deepcopy(ACTIVE_CONFIG)
        policy = AutoApplyPolicy(llm_enabled=True)
        results = generate_and_auto_apply(active, [item], policy)
        assert results == [None]  # action 不可逆 → L0 → None


def is_reversible_xpath(candidate: RepairCandidate) -> bool:
    """测试辅助：xpath 类型应可逆。"""
    return candidate.rule_type == "xpath"
