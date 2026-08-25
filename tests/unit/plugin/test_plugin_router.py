"""Phase 2a B4：Loader 路由矩阵契约测试。

验收锚点（方案 B4 / 第 26 轮）：
- subprocess 为缺省路径（含未声明 execution_mode）
- in_process 申请走批准矩阵；拒绝自动降级 subprocess（不拒载）
- 豁免表 expires 必填，过期条目无效
- runtime_backend 三态：auto / force_subprocess（总闸）/ legacy_in_process（逃生开关）
- 无头环境（approver=None）in_process 申请 fail-closed 降级
- 契约 1 一律 T3（第 67 轮）
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from omnicrawler.plugins import plugin_router as r


def test_subprocess_default_path() -> None:
    d = r.decide_route(execution_mode="subprocess")
    assert d.backend == "subprocess"

    # 未声明（空串）也走 subprocess
    d = r.decide_route(execution_mode="")
    assert d.backend == "subprocess"


def test_invalid_execution_mode_rejected() -> None:
    with pytest.raises(ValueError):
        r.decide_route(execution_mode="banana")


def test_t1_auto_grant() -> None:
    d = r.decide_route(
        execution_mode="in_process",
        maintainer_signed=True,
        gates_evidence=True,
        high_risk_capabilities=False,
        contract_version=2,
        approver=None,
    )
    assert d.backend == "in_process"
    assert d.tier == r.TIER_T1


def test_t2_requires_user_confirmation_headless_degrades() -> None:
    # 无 approver（无头）：T2 需确认 → fail-closed 降级 subprocess
    d = r.decide_route(
        execution_mode="in_process",
        maintainer_signed=True,
        gates_evidence=False,  # 证据不全 → T2
        contract_version=2,
        approver=None,
    )
    assert d.backend == "subprocess"
    assert d.tier == r.TIER_T2

    # 有 approver 且批准 → in_process
    d = r.decide_route(
        execution_mode="in_process",
        maintainer_signed=True,
        gates_evidence=False,
        contract_version=2,
        approver=lambda tier: True,
    )
    assert d.backend == "in_process"
    assert d.tier == r.TIER_T2

    # 有 approver 但拒绝 → 自动降级 subprocess（不拒载）
    d = r.decide_route(
        execution_mode="in_process",
        maintainer_signed=True,
        gates_evidence=False,
        contract_version=2,
        approver=lambda tier: False,
    )
    assert d.backend == "subprocess"
    assert d.tier == r.TIER_T2


def test_contract1_always_t3() -> None:
    # 第 67 轮：契约 1 一律 T3，即使 MaintainerSigned + 证据齐全
    d = r.decide_route(
        execution_mode="in_process",
        maintainer_signed=True,
        gates_evidence=True,
        contract_version=1,
        approver=lambda tier: True,
    )
    assert d.tier == r.TIER_T3
    assert d.backend == "in_process"


def test_approver_exception_treated_as_deny() -> None:
    def bad(tier):
        raise RuntimeError("boom")

    d = r.decide_route(
        execution_mode="in_process",
        maintainer_signed=True,
        contract_version=2,
        approver=bad,
    )
    assert d.backend == "subprocess"  # fail-closed


def test_allowlist_expiry_required_and_respected() -> None:
    now = datetime(2026, 8, 21, tzinfo=UTC)
    # expires 有效 → 强制 in_process（绕过批准矩阵）
    entry = {"expires": "2026-12-31T00:00:00+00:00"}
    d = r.decide_route(
        execution_mode="subprocess", allowlist_entry=entry, now=now,
    )
    assert d.backend == "in_process"
    assert d.tier == "allowlist"

    # 过期条目无效 → 回到默认 subprocess
    stale = {"expires": "2026-01-01T00:00:00+00:00"}
    d = r.decide_route(execution_mode="subprocess", allowlist_entry=stale, now=now)
    assert d.backend == "subprocess"

    # 缺 expires → 无效
    d = r.decide_route(execution_mode="subprocess", allowlist_entry={"expires": ""}, now=now)
    assert d.backend == "subprocess"


def test_force_subprocess_overrides_allowlist_and_in_process() -> None:
    entry = {"expires": "2026-12-31T00:00:00+00:00"}
    now = datetime(2026, 8, 21, tzinfo=UTC)
    for mode, allow in (("in_process", entry), ("in_process", None), ("subprocess", entry)):
        d = r.decide_route(
            execution_mode=mode,
            runtime_backend=r.RUNTIME_BACKEND_FORCE_SUBPROCESS,
            allowlist_entry=allow,
            now=now,
            approver=lambda tier: True,
        )
        assert d.backend == "subprocess", f"force_subprocess 总闸被绕过: {mode}/{allow}"


def test_legacy_in_process_escape_hatch() -> None:
    d = r.decide_route(
        execution_mode="subprocess",
        runtime_backend=r.RUNTIME_BACKEND_LEGACY_IN_PROCESS,
    )
    assert d.backend == "in_process"
    assert d.tier == "config"


def test_invalid_runtime_backend_rejected() -> None:
    with pytest.raises(ValueError):
        r.validate_runtime_backend("banana")
    assert r.validate_runtime_backend("auto") == r.RUNTIME_BACKEND_AUTO
    assert r.validate_runtime_backend("FORCE_SUBPROCESS") == r.RUNTIME_BACKEND_FORCE_SUBPROCESS


def test_detect_contract_shape() -> None:
    assert r.detect_contract_shape("def handle(op, p): return {}") == 2
    assert r.detect_contract_shape("def register(reg): pass") == 1
    # 两者共存按契约 2（可 subprocess）
    assert r.detect_contract_shape("def handle(op,p): return {}\ndef register(r): pass") == 2
    assert r.detect_contract_shape("x = 1") == 0
    assert r.detect_contract_shape("def broken(") == 0
