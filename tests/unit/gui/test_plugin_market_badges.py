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


def test_official_badge_is_not_invented() -> None:
    """★ 「官方」＝作者身份，需要市场侧发布官方认证作者数据源才能落。

    客户端不能自己发明判定（比如把 publisher 写死）——在那之前徽章里不许出现「官方」。
    """
    for entry in (
        _entry(publisher="starlife"),
        _entry(publisher="starlife", maintainer_package_signature_file="m.sig"),
    ):
        assert "官方" not in _badges(entry)
