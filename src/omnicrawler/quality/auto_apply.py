"""分级自动化引擎 — L0-L3 自动应用修复候选。

取代"一刀切人工批准"教条，用工程安全网（可逆性 + 审计 + 质量监控 + 观察期 + 置信度）
决定自动化等级。

自动化等级:
    L0 检测:        默认 / 用户未启用 LLM / 不可逆操作 → 仅产出候选，等待人工
    L1 幂等自动:     修复为幂等操作（trim/normalize_encoding/standardize_date）
                    → 立即应用 + 审计
    L2 高置信自动:   LLM 可用 + confidence ≥ 阈值 + improves_safely + 可逆
                    → 自动应用 + 进入 observing 观察期
    L3 持续自动:     L2 连续 3 轮 stable → 升级 stable，不再观察

安全边界:
    - 不可逆操作永远 L0（人工批准）
    - 自动应用必须写审计日志（approved_by 标记 auto:L1/L2/L3）
    - 自动应用必须保留 rollback_config_sha256（由 approve_repair 提供）
    - L2/L3 必须有质量监控，观察期质量下降 → 自动回滚

P3-3 新增（Observed 模式）：
    为解决「candidate.observation_rounds 只存在内存、process 重启就归零 → L3 永远达不到」
    的工程缺陷，本模块新增两个顶层便捷函数：

        1. observed_auto_apply(active, shadow, candidate, comparison, policy, store, actor="auto")
           → AutoApplyResult | None
           内部串 promote_for_candidate → classify_tier → auto_apply_if_safe → record_application。
           调用方无需自己维护 SQLite 记录。

        2. step_observe(candidate, latest_comparison, store, l2_regression_threshold=2)
           → RollbackDirective | None
           新一轮 shadow compare 后调用：更新 observation_rounds / EMA fp_risk / regression_count，
           达到回滚阈值时返回 RollbackDirective。

用法（新 P3-3 推荐模式）:
    from omnicrawler.quality.auto_apply import observed_auto_apply, step_observe
    from omnicrawler.quality.observation_store import ObservationStore

    with ObservationStore(config.workspace / "observation.sqlite3") as store:
        result = observed_auto_apply(active, shadow, candidate, comparison, policy, store)
        if result is None:
            # 需人工批准
            ...
        else:
            new_active = result.config
            # 下一轮 shadow compare:
            rollback = step_observe(candidate, next_comparison, store)
            if rollback is not None:
                new_active = rollback_to_sha(new_active, rollback.rollback_config_sha256)
                store.mark_rolled_back(candidate.candidate_id)
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from typing import Any

from .observation_store import ObservationStore, RollbackDirective
from .shadow_repair import (
    RepairCandidate,
    ShadowComparison,
    approve_repair,
)

LOGGER = logging.getLogger(__name__)

# ── 可逆性判断 ──────────────────────────────────────────────────────────

# 选择器替换类规则：总是可逆的（改回 old_rule 即还原）
_REVERSIBLE_RULE_TYPES: frozenset[str] = frozenset({"css", "xpath", "jsonpath"})

# "action" 类型可能触发不可逆副作用（如点击提交、删除），默认不可逆
_IRREVERSIBLE_RULE_TYPES: frozenset[str] = frozenset({"action"})


def is_reversible(rule_type: str) -> bool:
    """判断修复规则是否可逆。

    选择器替换（css/xpath/jsonpath）总是可逆：改回 old_rule 即还原。
    action 类型可能触发不可逆副作用，默认不可逆。

    Args:
        rule_type: 修复规则类型（css/xpath/jsonpath/action）

    Returns:
        True 表示可逆，False 表示不可逆
    """
    return rule_type in _REVERSIBLE_RULE_TYPES


# ── 自动化等级 ──────────────────────────────────────────────────────────


class AutomationTier(enum.Enum):
    """修复应用自动化等级。

    L0: 仅检测，产出候选等待人工批准
    L1: 幂等修复立即自动应用
    L2: 高置信 LLM 修复自动应用 + 观察期
    L3: L2 连续稳定后升级，不再观察
    """

    L0 = 0  # 检测 — 仅候选，人工批准
    L1 = 1  # 幂等自动 — 立即应用
    L2 = 2  # 高置信自动 — 应用 + 观察期
    L3 = 3  # 持续自动 — 稳定，不再观察


# ── 策略配置 ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AutoApplyPolicy:
    """分级自动化策略配置。

    所有阈值可在运行时调整，用于在不同部署环境（保守/激进）间切换。

    Attributes:
        llm_enabled: LLM 是否可用（False 时所有修复降级 L0/L1）
        l2_confidence_threshold: L2 自动应用所需最低置信度（默认 0.85）
        l2_max_false_positive_risk: L2 允许的最大误报风险（默认 0.15）
        l3_required_observation_rounds: L3 升级所需连续观察轮数（默认 3）
        l1_enabled: 是否启用 L1 幂等自动应用（默认 True）
        l2_enabled: 是否启用 L2 高置信自动应用（默认 True）
    """

    llm_enabled: bool = True
    l2_confidence_threshold: float = 0.85
    l2_max_false_positive_risk: float = 0.15
    l3_required_observation_rounds: int = 3
    l1_enabled: bool = True
    l2_enabled: bool = True


# ── 应用结果 ────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AutoApplyResult:
    """自动应用修复的结果。

    Attributes:
        tier: 实际应用的自动化等级
        config: 应用后的配置（含 _repair 审计元数据）
        candidate_id: 修复候选 ID
        reason: 选择该等级的原因（用于审计日志）
        rollback_available: 是否可回滚（True 表示保留了 rollback_config_sha256）
    """

    tier: AutomationTier
    config: dict[str, Any]
    candidate_id: str
    reason: str
    rollback_available: bool


# ── 核心逻辑 ────────────────────────────────────────────────────────────


def classify_tier(
    candidate: RepairCandidate,
    comparison: ShadowComparison,
    policy: AutoApplyPolicy,
) -> AutomationTier:
    """根据候选特征、质量比较和策略，决定自动化等级。

    决策逻辑（从高到低尝试，首个满足即返回）:
        1. 不可逆操作 → L0（安全兜底）
        2. 未通过安全改善检查 → L0（improves_safely 必须为 True）
        3. L3：candidate.stable（观察轮数 ≥3 且误报风险 ≤0.1）→ L3
        4. L2：LLM 可用 + confidence ≥ 阈值 + 误报风险 ≤ 上限 + 可逆 → L2
        5. L1：幂等修复（可逆选择器替换）+ l1_enabled + 非 LLM 来源 → L1
           （FINAL-S6：origin=="llm" 的候选置信不足 L2 时降级 L0 人工批准，
           防 prompt injection 经免审通道写入活跃配置）
        6. 默认 → L0

    Args:
        candidate: 修复候选
        comparison: 影子比较结果
        policy: 自动化策略

    Returns:
        匹配的自动化等级
    """
    # 安全兜底：不可逆操作永远 L0
    if not is_reversible(candidate.rule_type):
        return AutomationTier.L0

    # 安全兜底：未通过安全改善检查永远 L0
    if not comparison.improves_safely:
        return AutomationTier.L0

    # L3：连续稳定（观察轮数达标 + 误报风险低）
    # L3 是 L2 的稳定演化，需要 L2 启用（L2 禁用时稳定候选降级 L1/L0）
    if (
        policy.l2_enabled
        and candidate.observation_rounds >= policy.l3_required_observation_rounds
        and candidate.false_positive_risk <= 0.1
    ):
        return AutomationTier.L3

    # L2：高置信 LLM 修复
    if policy.l2_enabled and policy.llm_enabled:
        if (
            candidate.confidence >= policy.l2_confidence_threshold
            and candidate.false_positive_risk <= policy.l2_max_false_positive_risk
        ):
            return AutomationTier.L2

    # L1：幂等修复（可逆选择器替换）
    # FINAL-S6：LLM 生成的候选不得走 L1 免审通道——其规则内容源自不可信页面，
    # 本地验证数据同样来自该页面；置信不足 L2 时必须降级 L0 人工批准，
    # 否则构成 prompt injection → 配置持久化的最短路径。
    if (
        policy.l1_enabled
        and is_reversible(candidate.rule_type)
        and candidate.origin != "llm"
    ):
        return AutomationTier.L1

    # 默认：人工批准
    return AutomationTier.L0


def auto_apply_if_safe(
    active: dict[str, Any],
    shadow: dict[str, Any],
    candidate: RepairCandidate,
    comparison: ShadowComparison,
    policy: AutoApplyPolicy,
    *,
    actor: str = "auto",
) -> AutoApplyResult | None:
    """根据分级策略自动应用修复候选。

    主入口函数。当返回 None 时，调用方应回退到人工 approve_repair 流程。
    当返回 AutoApplyResult 时，修复已自动应用，config 字段是应用后的配置。

    自动应用内部调用 approve_repair，approved_by 标记为 "{actor}:L{tier}"，
    确保审计链完整。回滚通过 _repair.rollback_config_sha256 实现。

    Args:
        active: 当前活跃配置（不会被修改）
        shadow: 影子配置（由 shadow_config 生成）
        candidate: 修复候选
        comparison: 影子比较结果
        policy: 自动化策略
        actor: 审计日志中的操作者标识（默认 "auto"）

    Returns:
        AutoApplyResult 表示已自动应用；None 表示需人工批准
    """
    tier = classify_tier(candidate, comparison, policy)

    if tier == AutomationTier.L0:
        # L0：需人工批准
        LOGGER.debug(
            "修复候选 %s 分级为 L0（人工批准），原因: 不可逆/未安全改善/未达自动阈值",
            candidate.candidate_id,
        )
        return None

    # L1/L2/L3：自动应用，内部走 approve_repair 确保审计链完整
    reason = _tier_reason(tier, candidate, comparison)
    approved_by = f"{actor}:L{tier.value}"

    try:
        config = approve_repair(active, shadow, candidate, comparison, approved_by)
    except ValueError as exc:
        # approve_repair 内部检查未通过（如 improves_safely=False）
        # 理论上 classify_tier 已过滤，这里是双重保险
        LOGGER.warning(
            "修复候选 %s 自动应用失败（approve_repair 拒绝）: %s",
            candidate.candidate_id,
            exc,
        )
        return None

    result = AutoApplyResult(
        tier=tier,
        config=config,
        candidate_id=candidate.candidate_id,
        reason=reason,
        rollback_available=bool(config.get("_repair", {}).get("rollback_config_sha256")),
    )

    LOGGER.info(
        "修复候选 %s 自动应用为 L%d（原因: %s，可回滚: %s）",
        candidate.candidate_id,
        tier.value,
        reason,
        result.rollback_available,
    )
    return result


def _tier_reason(
    tier: AutomationTier,
    candidate: RepairCandidate,
    comparison: ShadowComparison,
) -> str:
    """生成选择该等级的人类可读原因（用于审计日志）。"""
    if tier == AutomationTier.L3:
        return f"连续 {candidate.observation_rounds} 轮稳定，误报风险 {candidate.false_positive_risk}"
    if tier == AutomationTier.L2:
        return f"置信度 {candidate.confidence} ≥ 阈值，质量 {comparison.old_quality}→{comparison.new_quality} 安全改善"
    if tier == AutomationTier.L1:
        return f"幂等修复（{candidate.rule_type} 选择器替换），质量改善 {comparison.old_quality}→{comparison.new_quality}"
    return "需人工批准"


def should_rollback_on_quality_drop(
    applied_result: AutoApplyResult,
    current_quality: float,
    baseline_quality: float,
) -> bool:
    """判断自动应用的修复是否应因质量下降而回滚。

    L2 观察期内若质量下降到基线以下，应自动回滚。
    L3 已稳定，但仍可监控；质量大幅下降（低于基线 90%）时回滚。

    Args:
        applied_result: 之前自动应用的结果
        current_quality: 当前质量分数
        baseline_quality: 应用前的基线质量分数

    Returns:
        True 表示应回滚
    """
    if applied_result.tier == AutomationTier.L2:
        # 观察期：质量降到基线以下即回滚
        return current_quality < baseline_quality
    if applied_result.tier == AutomationTier.L3:
        # 已稳定但仍监控：质量大幅下降（低于基线 90%）才回滚
        return current_quality < baseline_quality * 0.9
    # L1 幂等修复不回滚（操作本身可重跑还原）
    return False


# ── P3-3：Observed 模式 —— 观察期/L3 持久化顶层入口 ──────
def observed_auto_apply(
    active: dict[str, Any],
    shadow: dict[str, Any],
    candidate: RepairCandidate,
    comparison: ShadowComparison,
    policy: AutoApplyPolicy,
    store: ObservationStore,
    *,
    actor: str = "auto",
) -> AutoApplyResult | None:
    """P3-3 推荐入口：把 promote→classify→apply→record_application 串成一次调用。

    与裸 `auto_apply_if_safe` 的差异：
        * promote_for_candidate 先用持久化 rounds/fp_risk 替换 candidate 默认值
          → classiy_tier 的 L3 判断对 process 重启有记忆。
        * 若 L2/L3 自动应用，自动调用 record_application 写入 baseline_quality 和
          rollback_sha → 下一轮 step_observe 可据 EMA 发回滚指令。
    """
    promoted = store.promote_for_candidate(candidate)
    result = auto_apply_if_safe(active, shadow, promoted, comparison, policy, actor=actor)
    if result is None:
        return result
    # 只对 L2/L3 记录应用（L1 幂等没有观察期，也不回滚）
    if result.tier.value >= 2:
        store.record_application(
            promoted,
            tier_value=result.tier.value,
            baseline_quality=comparison.old_quality,
            rollback_config_sha256=result.config.get("_repair", {}).get("rollback_config_sha256")
            if isinstance(result.config, dict)
            else None,
        )
    return result


def step_observe(
    candidate: RepairCandidate,
    latest_comparison: ShadowComparison,
    store: ObservationStore,
    *,
    l2_regression_threshold: int = 2,
) -> RollbackDirective | None:
    """新一轮 shadow compare 完成后调用，推进 observation_rounds / EMA fp_risk / regression_count。

    Returns:
        当 regression_count >= l2_regression_threshold（默认连续 2 次影子比较不过关），
        返回 RollbackDirective；调用方应据此 revert active config，随后 mark_rolled_back。
    """
    promoted = store.promote_for_candidate(candidate)
    return store.record_observation(
        promoted, latest_comparison, l2_regression_threshold=l2_regression_threshold
    )

