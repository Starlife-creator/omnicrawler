"""插件运行模式路由裁决器（Phase 2a B4）。

三层纵深（方案第 26 轮）：① 信任审核认证来源；② OS 沙箱约束行为；
③ 批准矩阵授予进程内特权——本模块是 ③ 的裁决入口。

路由流程：
    验签（调用方已完成）→ 读 execution_mode
      → subprocess（含未声明，默认路径）→ 后端解析器 → 沙箱会话
      → in_process 申请 → 批准矩阵（T1/T2/T3）⊕ 用户确认
          批准 → 进程内；拒绝 → **自动降级 subprocess（不拒载）**

runtime_backend 三态（配置）：
    auto               按路由裁决（默认）
    force_subprocess   总闸：一切 subprocess（含 in_process 申请与豁免表）
    legacy_in_process  全局回退（逃生开关）：绕过批准矩阵直接进程内，须审计

豁免表（in_process_allowlist）：按插件强制进程内（用户显式决策，绕过批准
矩阵但不绕过 runtime_backend 三态）；expires 必填，过期条目无效。

无头/CLI：approver=None → in_process 申请一律拒绝（fail-closed 自动降级）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

LOGGER = logging.getLogger(__name__)

RUNTIME_BACKEND_AUTO = "auto"
RUNTIME_BACKEND_FORCE_SUBPROCESS = "force_subprocess"
RUNTIME_BACKEND_LEGACY_IN_PROCESS = "legacy_in_process"
_VALID_BACKENDS = {
    RUNTIME_BACKEND_AUTO,
    RUNTIME_BACKEND_FORCE_SUBPROCESS,
    RUNTIME_BACKEND_LEGACY_IN_PROCESS,
}

# 批准矩阵档位：T1 自动 / T2 自动验证+用户确认 / T3 最严格用户确认
TIER_T1 = "T1"
TIER_T2 = "T2"
TIER_T3 = "T3"


@dataclass(frozen=True, slots=True)
class RouteDecision:
    backend: str  # "subprocess" | "in_process"
    reason: str  # 可审计的裁决理由
    tier: str = ""  # "" | T1 | T2 | T3 | allowlist | config_override


def validate_runtime_backend(value: str) -> str:
    """配置校验：非法值拒绝（无兼容语义，B4 三态）。"""
    normalized = str(value or "").strip().casefold() or RUNTIME_BACKEND_AUTO
    if normalized not in _VALID_BACKENDS:
        raise ValueError(
            f"plugins.runtime_backend 非法: {value!r}（仅 auto | force_subprocess | legacy_in_process）"
        )
    return normalized


def _allowlist_entry_active(entry: dict[str, Any] | None, now: datetime | None = None) -> bool:
    """豁免表条目有效性：expires 必填且未过期（过期条目视为不存在）。"""
    if not entry:
        return False
    expires = str(entry.get("expires", "")).strip()
    if not expires:
        LOGGER.warning("豁免表条目缺少 expires，视为无效（expires 必填）")
        return False
    try:
        deadline = datetime.fromisoformat(expires.replace("Z", "+00:00"))
    except ValueError:
        LOGGER.warning("豁免表条目 expires 格式非法: %r", expires)
        return False
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=UTC)
    current = now or datetime.now(UTC)
    return current < deadline


def classify_in_process_tier(
    *,
    maintainer_signed: bool,
    gates_evidence: bool,
    high_risk_capabilities: bool,
    contract_version: int,
) -> str:
    """批准矩阵档位分类（3.2）。

    T1：MaintainerSigned + 门禁证据齐全 + 契约 2 + 无高危能力 → 自动批准候选
    T2：MaintainerSigned + 契约 2（证据不全或有高危能力）→ 自动验证+用户确认
    T3：其余（含契约 1 存量）→ 最严格，用户确认必须
    """
    if contract_version < 2:
        return TIER_T3  # 契约 1 一律 T3（第 67 轮：无能力声明，最高档审查）
    if maintainer_signed and gates_evidence and not high_risk_capabilities:
        return TIER_T1
    if maintainer_signed:
        return TIER_T2
    return TIER_T3


def decide_route(
    *,
    execution_mode: str,
    runtime_backend: str = RUNTIME_BACKEND_AUTO,
    allowlist_entry: dict[str, Any] | None = None,
    maintainer_signed: bool = False,
    gates_evidence: bool = False,
    high_risk_capabilities: bool = False,
    contract_version: int = 2,
    approver: Callable[[str], bool] | None = None,
    now: datetime | None = None,
) -> RouteDecision:
    """返回运行后端裁决（纯函数，可单测）。

    approver(tier) -> bool：批准矩阵用户确认回调；None = 无头/CLI（fail-closed）。
    """
    backend_cfg = validate_runtime_backend(runtime_backend)
    mode = str(execution_mode or "").strip() or "subprocess"
    if mode not in ("subprocess", "in_process"):
        raise ValueError(f"execution_mode 非法: {mode!r}（仅 in_process | subprocess）")

    # 总闸：force_subprocess 覆盖一切（含豁免表与 in_process 申请）
    if backend_cfg == RUNTIME_BACKEND_FORCE_SUBPROCESS:
        return RouteDecision("subprocess", "runtime_backend=force_subprocess 总闸", "config")

    # 逃生开关：legacy_in_process 绕过批准矩阵（须审计留痕，调用方负责）
    if backend_cfg == RUNTIME_BACKEND_LEGACY_IN_PROCESS:
        return RouteDecision(
            "in_process", "runtime_backend=legacy_in_process 全局回退（逃生开关）", "config"
        )

    # 豁免表：显式强制进程内（expires 必填，过期无效）
    if _allowlist_entry_active(allowlist_entry, now):
        return RouteDecision("in_process", "豁免表命中（expires 有效）", "allowlist")

    if mode == "subprocess":
        return RouteDecision("subprocess", "execution_mode=subprocess（默认路径）", "")

    # in_process 申请 → 批准矩阵
    tier = classify_in_process_tier(
        maintainer_signed=maintainer_signed,
        gates_evidence=gates_evidence,
        high_risk_capabilities=high_risk_capabilities,
        contract_version=contract_version,
    )
    if tier == TIER_T1:
        return RouteDecision("in_process", "批准矩阵 T1 自动批准", tier)
    if approver is None:
        # 无头/CLI：T2/T3 需要用户确认而环境无法提供 → fail-closed 降级
        return RouteDecision(
            "subprocess", f"批准矩阵 {tier} 需用户确认，无头环境 fail-closed 自动降级", tier
        )
    try:
        granted = bool(approver(tier))
    except Exception:  # noqa: BLE001 - 确认器异常按拒绝处理（fail-closed）
        LOGGER.exception("批准矩阵确认器异常，按拒绝处理")
        granted = False
    if granted:
        return RouteDecision("in_process", f"批准矩阵 {tier} 用户确认批准", tier)
    # 拒绝 → 自动降级 subprocess（不拒载，B4 核心语义）
    return RouteDecision("subprocess", f"批准矩阵 {tier} 用户拒绝，自动降级 subprocess", tier)


def resolve_runtime_backend(plugins_section: dict[str, Any] | None) -> tuple[str, bool]:
    """从配置解析 runtime_backend 与 sandbox_escape（B5 双通道）。

    返回 (backend, sandbox_escape)。
    - runtime_backend 非法 → 拒绝（validate_runtime_backend 抛错，调用方可映射 E_UNSUPPORTED_ENV）
    - sandbox_escape：配置键 或 环境变量 OMNICRAWL_ALLOW_UNSANDBOXED_PLUGIN=1（双通道）
    """
    import os

    section = plugins_section if isinstance(plugins_section, dict) else {}
    backend = validate_runtime_backend(str(section.get("runtime_backend", "auto")))
    escape = bool(section.get("sandbox_escape", False))
    if os.environ.get("OMNICRAWL_ALLOW_UNSANDBOXED_PLUGIN", "").strip() in ("1", "true", "yes"):
        escape = True
    return backend, escape


def detect_contract_shape(source: str) -> int:
    """静态检测插件契约形态（不执行代码，AST 顶层函数扫描）。

    返回契约版本号：
    - 契约 2：顶层定义 ``handle`` → 2（可 subprocess）
    - 契约 1：顶层定义 ``register`` 无 ``handle`` → 1（仅 in_process/legacy）
    - 两者皆无 → 0（非法，由调用方拒载）

    契约 1 不能以 subprocess 运行（register/继承在子进程无宿主注册面，
    方案第 17 轮）；此函数是加载器路由分流的静态依据。
    """
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    has_handle = has_register = False
    for node in tree.body:
        if isinstance(node, ast.FunctionDef):
            if node.name == "handle":
                has_handle = True
            elif node.name == "register":
                has_register = True
    if has_handle:
        return 2
    if has_register:
        return 1
    return 0
