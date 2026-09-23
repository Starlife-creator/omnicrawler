"""V2 设计系统深化验收（《优化方案》§11.2）。

四项各有可失败断言：
  * 字号/留白层级 —— 从**真实生成的 QSS** 与真实控件几何上取数；
  * 阴影收口 —— 层级表的每个取值都对照真实 effect（另见
    `test_gui_shared_helpers.py` 的"阴影参数只允许出现在 design_system"）；
  * 空态统一 —— 列表与空态**互斥**，且在真实列表页上生效；
  * 视觉快照 —— 见 `tests/gui/visual/`（本文件不重复渲染像素）。
"""

from __future__ import annotations

import re

import pytest
from PySide6.QtWidgets import QApplication, QListWidget, QTableWidget, QTreeWidget, QWidget

from omnicrawler.gui import design_system as ds
from omnicrawler.gui.design_system import (
    HERO_MIN_HEIGHT,
    LIGHT,
    SHADOW_LEVELS,
    SPACING,
    rgba_token_to_qcolor,
    scaled_font_px,
    shadow_effect,
    stylesheet,
)
from omnicrawler.gui.widgets.empty_state import EmptyState, sync_list_empty_state


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def _font_size_for(selector: str) -> int:
    pattern = rf"QLabel#{selector} \{{[^}}]*font-size:\s*(\d+)px"
    match = re.search(pattern, stylesheet(LIGHT))
    assert match, f"QSS 里找不到 {selector} 的字号声明"
    return int(match.group(1))


# ── 字号层级 ─────────────────────────────────────────────


def test_home_type_scale_comes_from_tokens_and_is_strictly_increasing() -> None:
    eyebrow = _font_size_for("eyebrow")
    lead = _font_size_for("homeLead")
    title = _font_size_for("homeTitle")

    assert eyebrow == scaled_font_px("small", scale=100)
    assert lead == scaled_font_px("subtitle", scale=100)
    assert title == scaled_font_px("hero", scale=100)
    assert eyebrow < lead < title


def test_home_type_scale_contrast_is_widened() -> None:
    """V2 的实质要求是"**加大**层级对比"，所以光有三级还不够。

    收敛前：eyebrow 12 → 标题 28（副标题借 muted 角色 = body 14）⇒ 标题/eyebrow ≈ 2.33。
    """
    eyebrow = _font_size_for("eyebrow")
    title = _font_size_for("homeTitle")
    assert title >= eyebrow * 2.5, "标题与 eyebrow 的层级差没有拉开"


def test_home_title_uses_the_top_scale_step() -> None:
    """标题取到字号刻度的**最大档**（hero），说明层级被主动拉开了。"""
    assert _font_size_for("homeTitle") == max(scaled_font_px(key, scale=100) for key in ds.FONT_SIZE)


# ── 留白层级 ─────────────────────────────────────────────


def test_hero_height_comes_from_token(qapp: QApplication) -> None:
    from omnicrawler.gui.home import AmbientHero

    hero = AmbientHero()
    try:
        assert hero.minimumHeight() == HERO_MIN_HEIGHT
        assert HERO_MIN_HEIGHT > 132, "hero 高度没有加大（原 132）"
    finally:
        hero._timer.stop()  # type: ignore[attr-defined]


def test_home_page_margins_and_spacing_come_from_tokens(qapp: QApplication) -> None:
    from omnicrawler.gui.home import HomePage

    page = HomePage()
    margins = page.layout().contentsMargins()  # type: ignore[union-attr]
    assert (margins.left(), margins.top(), margins.right(), margins.bottom()) == (
        SPACING["xxl"],
        SPACING["xl"],
        SPACING["xxl"],
        SPACING["xl"],
    )
    assert page.layout().spacing() == SPACING["lg"]  # type: ignore[union-attr]


# ── 阴影收口 ─────────────────────────────────────────────


#: 约定的阴影层级数值 —— ★ **写死在这里**，不从 `SHADOW_LEVELS` 里读。
#: 若从表里读，断言就变成"表等于它自己"（恒真）：把表改小也照样绿。
#: 这与"收敛前 home.py/toast.py 的两组内联数字"是同一份约定，属**有意变更**才改。
_EXPECTED_SHADOW_LEVELS = {"card": (24, 7), "overlay": (16, 4)}


@pytest.mark.parametrize("level", sorted(_EXPECTED_SHADOW_LEVELS))
def test_shadow_effect_matches_agreed_levels(qapp: QApplication, level: str) -> None:
    expected_blur, expected_offset = _EXPECTED_SHADOW_LEVELS[level]
    widget = QWidget()

    effect = shadow_effect(widget, level)

    assert effect.blurRadius() == expected_blur
    assert effect.offset().x() == 0
    assert effect.offset().y() == expected_offset
    # 取色必须来自**该层声明的令牌**（而不是写死色值）
    token_of = SHADOW_LEVELS[level][2]
    assert effect.color() == rgba_token_to_qcolor(token_of(ds.ThemeManager.instance().tokens))
    assert widget.graphicsEffect() is effect


def test_shadow_levels_use_the_declared_tokens() -> None:
    """层级 → 令牌的对应关系也钉住（换成别的令牌属有意变更）。"""
    assert SHADOW_LEVELS["card"][2](LIGHT) == LIGHT.card_shadow
    assert SHADOW_LEVELS["overlay"][2](LIGHT) == LIGHT.shadow_overlay


def test_shadow_effect_rejects_unknown_level(qapp: QApplication) -> None:
    """不认识的层级要报错，不能静默套一个"看起来差不多"的阴影。"""
    with pytest.raises(ValueError):
        shadow_effect(QWidget(), "definitely-not-a-level")


def test_shadow_levels_are_introspectable_and_distinct() -> None:
    assert set(SHADOW_LEVELS) == set(_EXPECTED_SHADOW_LEVELS)
    assert len({(blur, offset) for blur, offset, _ in SHADOW_LEVELS.values()}) == len(SHADOW_LEVELS)


# ── 空态统一 ─────────────────────────────────────────────


def _empty_state() -> EmptyState:
    return EmptyState(icon="✓", title="空", description="还没有内容")


def test_sync_list_empty_state_is_mutually_exclusive(qapp: QApplication) -> None:
    listing = QListWidget()
    empty = _empty_state()

    assert sync_list_empty_state(listing, empty) == 0
    assert listing.isHidden() is True
    assert empty.isHidden() is False

    listing.addItem("one")
    assert sync_list_empty_state(listing, empty) == 1
    assert listing.isHidden() is False
    assert empty.isHidden() is True


def test_sync_list_empty_state_counts_table_rows(qapp: QApplication) -> None:
    table = QTableWidget(0, 2)
    empty = _empty_state()
    assert sync_list_empty_state(table, empty) == 0
    table.setRowCount(3)
    assert sync_list_empty_state(table, empty) == 3
    assert table.isHidden() is False


def test_sync_list_empty_state_counts_tree_items(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QTreeWidgetItem

    tree = QTreeWidget()
    empty = _empty_state()
    assert sync_list_empty_state(tree, empty) == 0
    tree.addTopLevelItem(QTreeWidgetItem(["x"]))
    assert sync_list_empty_state(tree, empty) == 1


def test_sync_list_empty_state_rejects_unsupported_widget(qapp: QApplication) -> None:
    """不认识的控件要报错：静默当成 0 等于伪造"没有数据"这个结论。"""
    with pytest.raises(TypeError):
        sync_list_empty_state(QWidget(), _empty_state())


# ── 真实列表页上的空态 ───────────────────────────────────


def test_market_home_templates_pane_toggles_empty_state(tmp_path, qapp: QApplication) -> None:
    from omnicrawler.gui.views.market_home import _LocalTemplatesPane

    pane = _LocalTemplatesPane(tmp_path)
    pane.refresh()
    assert pane._list.count() == 0
    assert pane._list.isHidden() is True
    assert pane._empty_state.isHidden() is False

    template_dir = tmp_path / "templates" / "demo"
    template_dir.mkdir(parents=True)
    (template_dir / "template.yaml").write_text("name: demo\n", encoding="utf-8")
    pane.refresh()

    assert pane._list.count() == 1
    assert pane._list.isHidden() is False
    assert pane._empty_state.isHidden() is True


def test_market_home_plugins_pane_shows_empty_state(tmp_path, qapp: QApplication) -> None:
    from omnicrawler.gui.views.market_home import _LocalPluginsPane

    pane = _LocalPluginsPane(tmp_path, "private")
    pane.refresh()

    assert pane._list.isHidden() is True
    assert pane._empty_state.isHidden() is False


def test_template_market_shows_empty_state_without_catalog(tmp_path, qapp: QApplication) -> None:
    from omnicrawler.gui.views.template_market import TemplateMarketView

    view = TemplateMarketView("", str(tmp_path), "")
    view._populate_list()

    assert view._list.count() == 0
    assert view._list.isHidden() is True
    assert view._empty_state.isHidden() is False
