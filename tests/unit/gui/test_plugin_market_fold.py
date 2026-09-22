"""细节折叠的门禁 —— 折叠区只放技术细节，永不折叠项必须在折叠区外（§10.5）。"""

from __future__ import annotations

from omnicrawler.gui.views.plugin_market_logic import (
    NEVER_FOLDED_LABELS,
    _technical_details,
)


def _full_entry() -> dict[str, object]:
    return {
        "id": "demo",
        "package_manifest_sha256": "a" * 64,
        "license": "MIT",
        "compatible_core": ">=0.12.0",
        "permissions": ["files:read", "secrets:read"],
        "domains": ["example.com"],
    }


def test_never_folded_labels_are_declared() -> None:
    assert "风险" in NEVER_FOLDED_LABELS
    assert "审核状态" in NEVER_FOLDED_LABELS


def test_technical_details_extract_expected_fields() -> None:
    details = dict(_technical_details(_full_entry()))
    assert details["插件 ID"] == "demo"
    assert details["包清单哈希"] == "a" * 64
    assert "secrets:read" in details["完整权限"]
    assert details["允许域名"] == "example.com"


def test_technical_details_never_contain_never_folded_items() -> None:
    """★ 门禁核心：风险/审核状态等永不折叠项，不得被放进食折叠区。"""
    for label, _value in _technical_details(_full_entry()):
        for forbidden in NEVER_FOLDED_LABELS:
            assert forbidden not in label, f"{label} 属于永不折叠项，不能进折叠区"


def test_empty_entry_yields_no_details() -> None:
    assert _technical_details({}) == []
