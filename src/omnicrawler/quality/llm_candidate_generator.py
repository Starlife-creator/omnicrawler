"""LLM 影子修复候选生成器 — 把 LLM 重新生成的选择器转换为 RepairCandidate。

作为 shadow_repair 与 auto_apply 的桥梁：
    RepairProposal (adaptive_extractor 产出)
        ↓ proposal_to_candidate 转换
    RepairCandidate (shadow_repair 消费)
        ↓ auto_apply_if_safe 分级应用
    AutoApplyResult | None

设计原则:
    - 不修改 shadow_repair.py / adaptive_extractor.py
    - 复用 adaptive_extractor 的 LLM 调用与本地验证逻辑
    - 复用 shadow_repair 的 candidate_rule / RepairCandidate 数据结构
    - 复用 auto_apply 的 L0-L3 分级自动化

安全边界（对齐 adaptive_extractor.py）:
    - 页面内容经 mark_untrusted 标记，禁止当作指令执行
    - LLM 输出经 validate_ai_output 白名单校验
    - 生成的规则长度受限（4000 字符）
    - 本地验证命中数达标才采纳（MIN_MATCHES=1）

用法:
    from omnicrawler.quality.llm_candidate_generator import (
        LLMCandidateGenerator,
        generate_and_auto_apply,
    )
    from omnicrawler.quality.auto_apply import AutoApplyPolicy

    generator = LLMCandidateGenerator()
    candidates = generator.generate_candidates(html, records, fields)
    policy = AutoApplyPolicy(llm_enabled=True)
    results = generate_and_auto_apply(active, candidates, generator.comparisons, policy)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..extraction.adaptive_extractor import AdaptiveExtractor, RepairProposal
from .auto_apply import AutoApplyPolicy, AutoApplyResult, auto_apply_if_safe
from .shadow_repair import (
    RepairCandidate,
    ShadowComparison,
    candidate_rule,
    shadow_config,
)

LOGGER = logging.getLogger(__name__)

# LLM 生成候选时，每个字段至少需要的支持样本数（用于 candidate_rule 的 confidence 计算）
# adaptive_extractor.verify_rule 最多返回 5 个样本值，这里取全部作为支持样本
_MIN_SUPPORTING_SAMPLES = 1


@dataclass(frozen=True, slots=True)
class CandidateWithComparison:
    """一个修复候选及其对应的影子比较结果。

    Attributes:
        candidate: 修复候选（含置信度、支持样本）
        comparison: 影子比较结果（基于验证数据构造）
    """

    candidate: RepairCandidate
    comparison: ShadowComparison


def proposal_to_candidate(
    proposal: RepairProposal,
    *,
    observation_rounds: int = 0,
) -> RepairCandidate | None:
    """把 AdaptiveExtractor 产出的 RepairProposal 转换为 RepairCandidate。

    转换逻辑:
        - field / rule_type / old_rule / new_rule 直接映射
        - supporting_samples = proposal.sample_values（验证命中的值样本）
        - counterexamples = ()（本地验证通过即无反例）
        - confidence 由 candidate_rule 内部公式计算

    Args:
        proposal: adaptive_extractor 产出的修复建议
        observation_rounds: 观察轮数（首次生成=0）

    Returns:
        RepairCandidate；若 proposal 未通过验证或样本不足则返回 None
    """
    if not proposal.verified:
        LOGGER.debug("RepairProposal 未通过验证，跳过: %s", proposal.field)
        return None
    if len(proposal.sample_values) < _MIN_SUPPORTING_SAMPLES:
        LOGGER.debug(
            "RepairProposal 支持样本不足 (%d < %d)，跳过: %s",
            len(proposal.sample_values),
            _MIN_SUPPORTING_SAMPLES,
            proposal.field,
        )
        return None

    # candidate_rule 内部计算 confidence = n/(n+2) * (1 - risk)
    # proposal.sample_values 全部是验证命中的真实值，作为支持样本
    candidate = candidate_rule(
        field=proposal.field,
        rule_type=proposal.rule_type,
        old_rule=proposal.old_rule,
        new_rule=proposal.new_rule,
        supporting=proposal.sample_values,
        counterexamples=(),
    )

    # 如果需要设置 observation_rounds，重新构造（candidate_rule 默认 0）
    if observation_rounds > 0:
        candidate = RepairCandidate(
            candidate_id=candidate.candidate_id,
            field=candidate.field,
            rule_type=candidate.rule_type,
            old_rule=candidate.old_rule,
            new_rule=candidate.new_rule,
            confidence=candidate.confidence,
            supporting_samples=candidate.supporting_samples,
            counterexamples=candidate.counterexamples,
            expected_recovery=candidate.expected_recovery,
            false_positive_risk=candidate.false_positive_risk,
            observation_rounds=observation_rounds,
        )
    return candidate


def build_comparison_from_proposal(
    proposal: RepairProposal,
    *,
    old_quality: float = 0.0,
    new_quality: float = 0.0,
) -> ShadowComparison:
    """基于 RepairProposal 的验证数据构造 ShadowComparison。

    adaptive_extractor 的本地验证已经确保新规则命中数达标，
    这里把命中数转换为质量改善指标：
        - old_records / new_records: 用 proposal.matches 表示新规则命中数
        - old_quality / new_quality: 调用方传入（默认 0.0 表示未知，需调用方填充）
        - false_matches: 本地验证通过即 0
        - historical_compatible: True（新规则在当前页面验证通过）

    Args:
        proposal: 修复建议
        old_quality: 旧规则质量分数（如字段成功率）
        new_quality: 新规则质量分数

    Returns:
        ShadowComparison 实例
    """
    return ShadowComparison(
        old_records=proposal.matches,
        new_records=proposal.matches,
        old_quality=old_quality,
        new_quality=new_quality,
        false_matches=0,
        historical_compatible=True,
    )


class LLMCandidateGenerator:
    """LLM 驱动的修复候选生成器。

    封装 AdaptiveExtractor 的 LLM 调用与本地验证，产出可直接接入
    auto_apply_if_safe 的 CandidateWithComparison 列表。

    Args:
        llm_generate: 可选；同步 LLM 调用函数，签名 (prompt: str) -> str。
            缺省时使用 provider_from_env() 构造的 OpenAI 兼容 provider。
        success_threshold: 字段成功率低于该值判为失效（0~1）
        max_prompt_chars: 送入 LLM 的页面降维内容上限
    """

    def __init__(
        self,
        llm_generate: Callable[[str], str] | None = None,
        *,
        success_threshold: float = 0.7,
        max_prompt_chars: int = 4000,
        project_root: str | None = None,
    ) -> None:
        self._extractor = AdaptiveExtractor(
            llm_generate=llm_generate,
            success_threshold=success_threshold,
            max_prompt_chars=max_prompt_chars,
            project_root=project_root,
        )
        self._last_candidates: list[CandidateWithComparison] = []

    @property
    def comparisons(self) -> list[ShadowComparison]:
        """最近一次 generate_candidates 产出的影子比较结果列表。"""
        return [item.comparison for item in self._last_candidates]

    @property
    def candidates(self) -> list[RepairCandidate]:
        """最近一次 generate_candidates 产出的候选列表。"""
        return [item.candidate for item in self._last_candidates]

    def generate_candidates(
        self,
        html: str,
        records: list[Any],
        fields: dict[str, Any],
        *,
        old_quality: float = 0.0,
        new_quality: float = 0.0,
        observation_rounds: int = 0,
    ) -> list[CandidateWithComparison]:
        """从失效字段生成 LLM 修复候选。

        流程:
            1. 调用 AdaptiveExtractor.propose_repairs 检测失效字段 + LLM 生成 + 本地验证
            2. 把通过验证的 RepairProposal 转换为 RepairCandidate
            3. 构造对应的 ShadowComparison
            4. 返回 CandidateWithComparison 列表

        Args:
            html: 目标页面 HTML
            records: 该页面的提取记录
            fields: extract.fields 配置
            old_quality: 旧规则质量分数（用于 ShadowComparison）
            new_quality: 新规则质量分数
            observation_rounds: 观察轮数（首次生成=0）

        Returns:
            CandidateWithComparison 列表；无 AI 或验证不过时返回空列表
        """
        proposals = self._extractor.propose_repairs(html, records, fields)
        if not proposals:
            LOGGER.info("AdaptiveExtractor 未产出通过验证的修复建议")
            self._last_candidates = []
            return []

        results: list[CandidateWithComparison] = []
        for proposal in proposals:
            candidate = proposal_to_candidate(
                proposal, observation_rounds=observation_rounds
            )
            if candidate is None:
                continue
            comparison = build_comparison_from_proposal(
                proposal,
                old_quality=old_quality,
                new_quality=new_quality,
            )
            results.append(CandidateWithComparison(candidate, comparison))

        self._last_candidates = results
        LOGGER.info(
            "LLM 候选生成完成：%d 个提议 → %d 个候选",
            len(proposals),
            len(results),
        )
        return results


def generate_and_auto_apply(
    active: dict[str, Any],
    candidates_with_comparisons: list[CandidateWithComparison],
    policy: AutoApplyPolicy,
    *,
    actor: str = "auto",
) -> list[AutoApplyResult | None]:
    """批量生成候选并按分级策略自动应用。

    对每个 CandidateWithComparison:
        - 构造 shadow_config
        - 调用 auto_apply_if_safe
        - 收集结果（None 表示需人工批准）

    Args:
        active: 当前活跃配置（不会被修改）
        candidates_with_comparisons: 候选 + 比较结果列表
        policy: 自动化策略
        actor: 审计日志中的操作者标识

    Returns:
        AutoApplyResult | None 列表，与输入一一对应；
        非 None 表示已自动应用，None 表示需人工批准
    """
    results: list[AutoApplyResult | None] = []
    for item in candidates_with_comparisons:
        shadow = shadow_config(active, item.candidate)
        result = auto_apply_if_safe(
            active,
            shadow,
            item.candidate,
            item.comparison,
            policy,
            actor=actor,
        )
        results.append(result)
    return results
