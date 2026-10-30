"""tombstones（撤销下架）可见性的自测 —— 已下架插件不得静默消失（§4.6 第 3 条）。"""

from __future__ import annotations

from omnicrawler.gui.views.plugin_market_logic import (
    _install_block_reason,
    _tombstone_reason,
)

_CATALOG = {
    "tombstones": [
        {"id": "removed-plugin", "removed_at": "2026-09-01", "reason": "作者申请下架"},
        {"id": "no-reason-plugin", "removed_at": "2026-09-02"},
    ]
}


def test_tombstoned_id_returns_reason() -> None:
    text = _tombstone_reason(_CATALOG, "removed-plugin")
    assert text and "已下架" in text and "2026-09-01" in text and "作者申请下架" in text


def test_tombstone_without_reason_still_names_the_date() -> None:
    text = _tombstone_reason(_CATALOG, "no-reason-plugin")
    assert text and "已下架" in text and "2026-09-02" in text


def test_live_plugin_returns_none() -> None:
    assert _tombstone_reason(_CATALOG, "lumen-drift") is None


def test_catalog_without_tombstones_returns_none() -> None:
    """当前真实市场还没有 tombstones 字段 ⇒ 必须安全返回 None，不得崩。"""
    assert _tombstone_reason({}, "removed-plugin") is None
    assert _tombstone_reason({"tombstones": None}, "removed-plugin") is None
    assert _tombstone_reason({"tombstones": "oops"}, "removed-plugin") is None


# --- P0-2：安装按钮/动作在吊销与墓碑命中时即阻断（展示层 G2 裁决）---
def test_block_reason_denies_entry_level_revoked() -> None:
    entry = {"id": "demo", "version": "1.0.0", "plugin_types": ["processor"],
             "revoked": {"reason": "恶意代码"}}
    reason = _install_block_reason(entry)
    assert reason and "吊销" in reason and "恶意代码" in reason


def test_block_reason_denies_revoked_version() -> None:
    entry = {"id": "demo", "version": "1.0.0", "plugin_types": ["processor"],
             "revoked_versions": ["1.0.0"]}
    assert _install_block_reason(entry)


def test_block_reason_denies_catalog_tombstone() -> None:
    entry = {"id": "removed-plugin", "version": "1.0.0", "plugin_types": ["processor"]}
    reason = _install_block_reason(entry, catalog=_CATALOG, plugin_id="removed-plugin")
    assert reason and "已下架" in reason


def test_block_reason_allows_live_entry() -> None:
    entry = {"id": "lumen-drift", "version": "1.0.0", "plugin_types": ["processor"]}
    assert _install_block_reason(entry, catalog=_CATALOG, plugin_id="lumen-drift") == ""
