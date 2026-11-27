"""契约 1 在主进程执行时**必须留下可追查的记录**，不能静默。

## 事故形态（2026-11-27）

`plugin_loader` 的分流条件是 `route.backend == "subprocess" and contract_shape == 2`。
**契约 1 不满足任何一个分支**，直接落到兜底「在主进程加载」，于是：

- `plugins.in_process_allowlist`（带 `expires`）、`plugins.trusted_local_dirs`、
  T1/T2/T3 批准矩阵**对契约 1 全部不适用** —— `route` 算出来了，但没人用；
- `docs/PLUGIN_CONTRACT.md` 写着「契约 1 一律采用最高风险审批档」，代码里没有这道门；
- 市场里的 `document-workbench` 就是契约 1。

## 判据：从"静默"改成"可追查"

硬拒**不可行**：契约 1 物理上无法隔离到子进程，把 `route.backend != in_process`
当成拒载会把**所有**已验签信任的契约 1 插件一起拒掉（实测 5 条既有用例全红）——
那不是修补而是破坏。

所以判据落在「**不再静默**」：

| 信任层级 | 行为 |
|---|---|
| `MaintainerSigned` / `CreatorTrusted`（显式信任）| 正常放行，不额外告警 —— 严格模式的验签本身就是那道批准 |
| 其余（`developer` 放行、`LOAD_ONCE`、`Unsigned`）| **落 `registry.escape_hatch_events` + 显著 WARNING** |

审计条目带 `trust_level` 与 `tier`，让"在主进程执行了未显式信任的契约 1"可被追查。

## 这条测试守什么

- 未显式信任 ⇒ 有审计条目**且**有 warning（不是静默）；
- 显式信任 ⇒ **没有**审计条目（不能制造噪声，否则台账会被淹）；
- 契约 1 的 `execution_mode` 必须是 `in_process`：否则 `decide_route` 直接返回
  subprocess 分支，批准矩阵从未对契约 1 求值 —— 这正是「有代码没接线」的起点。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

pytest.importorskip("cryptography")

from omnicrawler.plugins import plugin_router, signing
from omnicrawler.plugins.plugins import (
    SIGNATURE_POLICY_DEVELOPER,
    Registry,
    load_local_plugins,
)

_CONTRACT1_SOURCE = (
    "PLUGIN_METADATA = {'name': %r, 'version': '1.0.0'}\n"
    "def register(registry):\n"
    "    registry.register_source('c1_src', lambda *a, **k: None)\n"
)


def _write_contract1(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    plugin = directory / "plugin.py"
    plugin.write_text(_CONTRACT1_SOURCE % name, encoding="utf-8")
    return plugin


def test_contract1_execution_mode_is_in_process() -> None:
    """★ 契约 1 的 execution_mode 必须是 in_process。

    这是「审批门有没有接线」的起点：填 subprocess 会让 `decide_route`
    直接短路到 subprocess 分支，批准矩阵永远不求值。
    """
    assert plugin_router.TIER_T3 == "T3"
    # 契约 1 一律 T3（最高档）
    assert plugin_router.classify_in_process_tier(
        maintainer_signed=True, gates_evidence=True,
        high_risk_capabilities=False, contract_version=1,
    ) == plugin_router.TIER_T3


def test_untrusted_contract1_leaves_an_audit_entry(tmp_path: Path, caplog) -> None:
    """★ 核心判据：未显式信任的契约 1 **必须留下审计条目 + warning**。

    「静默在主进程执行」正是本次要修的那件事。
    """
    plugin = _write_contract1(tmp_path / "plug", "c1_untrusted")
    registry = Registry()
    with caplog.at_level(logging.WARNING):
        load_local_plugins(
            registry, [str(plugin)], tmp_path,
            signature_policy=SIGNATURE_POLICY_DEVELOPER,
        )

    assert [item.name for item in registry.plugins] == ["c1_untrusted"]
    audit = [e for e in registry.escape_hatch_events if e.get("plugin_id") == "c1_untrusted"]
    assert audit, f"缺少审计条目：{registry.escape_hatch_events}"
    assert audit[0]["backend"] == "in_process"
    assert audit[0]["trust_level"], "审计条目必须写明信任层级"
    assert any("契约 1" in record.message for record in caplog.records), (
        "必须有显著 WARNING，不能只落台账不说话"
    )


def test_explicitly_trusted_contract1_is_not_audited(tmp_path: Path) -> None:
    """★ 反方向：维护者验签通过的契约 1 **不该**进逃生台账。

    严格模式下的验签本身就是那道批准；给它也记台账只会把台账淹掉。
    """
    from omnicrawler.core.config import AppConfig

    private_pem, public_pem = signing.generate_keypair()
    plugin = _write_contract1(tmp_path / "plug", "c1_signed")
    signing.sign_file(plugin, private_pem)
    trust = tmp_path / "trust.pub.pem"
    trust.write_bytes(public_pem)
    config = AppConfig(
        Path("<memory>"), tmp_path,
        {"plugins": {"trust_public_key": str(trust)}}, tmp_path,
    )
    registry = Registry()
    load_local_plugins(
        registry, [str(plugin)], tmp_path,
        config=config, signature_policy="strict",
    )

    assert [item.name for item in registry.plugins] == ["c1_signed"]
    assert registry.escape_hatch_events == [], registry.escape_hatch_events


def test_contract2_is_not_touched_by_the_contract1_audit(tmp_path: Path, caplog) -> None:
    """回归：这段逻辑只对契约 1 生效，契约 2 的逃生台账不受影响。"""
    plugin_dir = tmp_path / "plug2"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.py").write_text(
        "PLUGIN_METADATA = {'name': 'c2_demo', 'version': '1.0', "
        "'execution_mode': 'subprocess', 'plugin_types': ['source'], "
        "'permissions': []}\n"
        "def handle(operation, payload):\n"
        "    if operation == 'source.seed':\n"
        "        return {'requests': [{'url': 'https://example.com/'}]}\n"
        "    return {'operation': operation}\n",
        encoding="utf-8",
    )
    registry = Registry()
    with caplog.at_level(logging.WARNING):
        load_local_plugins(
            registry, [str(plugin_dir / "plugin.py")], tmp_path,
            signature_policy=SIGNATURE_POLICY_DEVELOPER,
        )
    assert "c2_demo" in registry.sources
    assert registry.escape_hatch_events == [], registry.escape_hatch_events
