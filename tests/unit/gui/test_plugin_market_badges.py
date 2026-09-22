"""市场徽章的自测 —— M5 裁定：官方＝作者身份、已审核＝流程状态，两个维度非互斥。"""

from __future__ import annotations

from omnicrawler.gui.views.plugin_market_logic import _badges, _reviewed


def _entry(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "demo",
        "permissions": [],
        "execution_mode": "subprocess",
    }
    base.update(overrides)
    return base


def test_reviewed_requires_maintainer_signature() -> None:
    assert _reviewed(_entry(maintainer_package_signature_file="plugins/x/maintainer.sig"))
    assert not _reviewed(_entry())
    assert not _reviewed(_entry(maintainer_package_signature_file=""))


def test_reviewed_and_high_risk_badges_are_both_shown() -> None:
    """M5 的关键回归：两个维度**非互斥**——已审核的高风险插件必须同时亮两个徽章。"""
    entry = _entry(
        maintainer_package_signature_file="plugins/x/maintainer.sig",
        permissions=["secrets:read"],
    )
    assert _badges(entry) == ("已审核", "高权限")


def test_low_risk_unreviewed_has_no_badges() -> None:
    assert _badges(_entry()) == ()


def test_high_risk_via_in_process_mode() -> None:
    entry = _entry(execution_mode="in_process", maintainer_package_signature_file="m.sig")
    assert _badges(entry) == ("已审核", "高权限")


def test_official_badge_from_catalog_list() -> None:
    """★ 数据源已落地（2026-09-22 维护者拍板）：catalog 顶层 official_publishers。"""
    entry = _entry(publisher="Starlife", maintainer_package_signature_file="m.sig")
    badges = _badges(entry, {"official_publishers": ["starlife"]})
    assert badges[0] == "官方"
    assert "已审核" in badges


def test_official_badge_requires_listed_publisher() -> None:
    entry = _entry(publisher="someone-else", maintainer_package_signature_file="m.sig")
    assert "官方" not in _badges(entry, {"official_publishers": ["starlife"]})


def test_no_official_badge_on_legacy_catalog() -> None:
    """旧版 catalog 没有 official_publishers ⇒ 一律非官方（向后兼容，不写死推断）。"""
    entry = _entry(publisher="starlife")
    assert "官方" not in _badges(entry)
    assert "官方" not in _badges(entry, {})


def test_official_reviewed_highrisk_all_coexist() -> None:
    """M5 三个维度非互斥：官方 + 已审核 + 高权限 同时出现，官方排第一。"""
    entry = _entry(
        publisher="starlife",
        maintainer_package_signature_file="m.sig",
        permissions=["secrets:read"],
    )
    assert _badges(entry, {"official_publishers": ["starlife"]}) == ("官方", "已审核", "高权限")
