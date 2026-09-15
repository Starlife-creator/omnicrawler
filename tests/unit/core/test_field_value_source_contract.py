"""字段「取值位置」契约：推导 / 归一 / 校验 **与引擎逐条对齐**。

## 这个契约要解决的问题（2026-09-15）

引擎**支持**三种取值位置（见 ``extraction/extractors.py``）：
``selector`` 空 ⇒ 以**条目元素自身**为上下文；``attr`` 有 ⇒ 取属性、无 ⇒ 取文本。
但 GUI 只认得出"选择器非空"这一种（``FieldDef.validate(require_selector=True)`` 报
「选择器不能为空」），且键名两边不同（GUI 叫 ``attribute``、运行期叫 ``attr``）。

因此本文件的重头是最后那条 **契约↔引擎对齐**：把契约声明的形状真的喂给引擎，
断言它取到的值就是契约承诺的那个 —— 否则契约只是纸面文字。
"""

from __future__ import annotations

import pytest

from omnicrawler.core.field_value_source import (
    ATTR_KEY,
    ATTRIBUTE_ALIAS,
    COMMON_ATTRIBUTES,
    POSITION_CHILD,
    POSITION_ELEMENT,
    POSITION_ELEMENT_ATTR,
    POSITIONS,
    SELECTOR_KEY,
    normalize_rule,
    position_of,
    to_rule,
    validate_value_source,
)

_ITEM_HTML = """
<html><body>
  <ul class="list">
    <li class="item"><a class="link" href="/a/1">第一条</a><span class="p">11</span></li>
    <li class="item"><a class="link" href="/a/2">第二条</a><span class="p">22</span></li>
  </ul>
  <div class="links">
    <a class="beacon" href="/b/1" data-kind="primary">第三条</a>
    <a class="beacon" href="/b/2" data-kind="secondary">第四条</a>
  </div>
</body></html>
"""


# ── 位置推导（零迁移）─────────────────────────────────────────────────────


def test_position_is_derived_from_the_existing_shape() -> None:
    """旧配置不需要新增任何键：位置完全由 `selector` / `attr` 推导。"""
    assert position_of({SELECTOR_KEY: "span.p"}).key == POSITION_CHILD
    assert position_of({SELECTOR_KEY: "span.p", ATTR_KEY: "href"}).key == POSITION_CHILD
    assert position_of({SELECTOR_KEY: ""}).key == POSITION_ELEMENT
    assert position_of({SELECTOR_KEY: "   "}).key == POSITION_ELEMENT
    assert position_of({SELECTOR_KEY: "", ATTR_KEY: "href"}).key == POSITION_ELEMENT_ATTR
    assert position_of({}).key == POSITION_ELEMENT  # 完全没有键 ⇒ 取自身文本


def test_every_position_is_exported_with_a_summary() -> None:
    assert {position.key for position in POSITIONS} == {POSITION_CHILD, POSITION_ELEMENT, POSITION_ELEMENT_ATTR}
    for position in POSITIONS:
        assert position.summary, position.key
        assert not (position.needs_selector and position.needs_attribute), "三种位置互斥"


# ── 生成运行期形状 / 归一键名 ─────────────────────────────────────────────


def test_to_rule_emits_the_runtime_shape() -> None:
    assert to_rule(POSITION_CHILD, selector="span.p") == {SELECTOR_KEY: "span.p"}
    assert to_rule(POSITION_CHILD, selector="span.p", attr="href") == {
        SELECTOR_KEY: "span.p",
        ATTR_KEY: "href",
    }
    # 取元素自身文本：**不留 attr**（否则会变成取属性）
    assert to_rule(POSITION_ELEMENT, selector="span.p", attr="href") == {SELECTOR_KEY: ""}
    # 取元素自身属性：选择器留空、属性名必填（缺省给最常见的 href）
    assert to_rule(POSITION_ELEMENT_ATTR, selector="span.p") == {SELECTOR_KEY: "", ATTR_KEY: "href"}
    assert to_rule(POSITION_ELEMENT_ATTR, attr="src") == {SELECTOR_KEY: "", ATTR_KEY: "src"}


def test_to_rule_rejects_unknown_position() -> None:
    with pytest.raises(ValueError, match="未知的取值位置"):
        to_rule("self")


def test_normalize_rule_converges_the_two_key_names() -> None:
    """`attribute`（GUI/分析侧旧名）→ `attr`（运行期名），其余键原样保留。"""
    assert normalize_rule({SELECTOR_KEY: "", ATTRIBUTE_ALIAS: "href"}) == {SELECTOR_KEY: "", ATTR_KEY: "href"}
    # 空 alias 不应留下 `attr` 键
    assert normalize_rule({SELECTOR_KEY: "span.p", ATTRIBUTE_ALIAS: ""}) == {SELECTOR_KEY: "span.p"}
    # selector 兜底为 ""（引擎对缺失键的容错就是这样）
    assert normalize_rule({ATTR_KEY: "href"}) == {SELECTOR_KEY: "", ATTR_KEY: "href"}
    # 其它引擎键不得被吞掉
    other = {SELECTOR_KEY: "span.p", "regex": r"(?P<value>\d+)", "transforms": ["strip"], "default": "-"}
    assert normalize_rule(other) == other


# ── 校验（含反向检查）─────────────────────────────────────────────────────


def test_validate_accepts_every_legal_shape() -> None:
    for rule in (
        {SELECTOR_KEY: "span.p"},
        {SELECTOR_KEY: "span.p", ATTR_KEY: "href"},
        {SELECTOR_KEY: ""},
        {SELECTOR_KEY: "", ATTR_KEY: "href"},
        {SELECTOR_KEY: "", ATTR_KEY: "data-custom"},
    ):
        assert validate_value_source(rule) == [], rule


def test_validate_reports_structural_problems() -> None:
    assert validate_value_source("span.p") == ["字段规则必须是YAML对象"]
    assert any("缺少" in issue for issue in validate_value_source({ATTR_KEY: "href"}))
    assert any("必须是字符串" in issue for issue in validate_value_source({SELECTOR_KEY: 3}))
    assert any("必须是字符串" in issue for issue in validate_value_source({SELECTOR_KEY: "", ATTR_KEY: 3}))
    assert any("旧键名" in issue for issue in validate_value_source({SELECTOR_KEY: "", ATTRIBUTE_ALIAS: "href"}))


def test_validate_catches_attribute_written_as_selector() -> None:
    """**反向检查**：把属性名写进选择器 ⇒ 引擎会把它当 CSS 选择器找，**静默取不到值**。

    这正是"取元素自身属性"最容易犯的错（`selector: href` 而不是 `selector: ""` + `attr: href`），
    所以要在保存前就拦下来，而不是让用户拿到空列。
    """
    for name in COMMON_ATTRIBUTES:
        issues = validate_value_source({SELECTOR_KEY: name})
        assert issues and "留空" in issues[0], (name, issues)
    # 正常选择器不得被误伤（哪怕它长得像单词）
    assert validate_value_source({SELECTOR_KEY: "span.p"}) == []
    assert validate_value_source({SELECTOR_KEY: "a.link"}) == []
    assert validate_value_source({SELECTOR_KEY: "#main"}) == []


# ── ★ 契约↔引擎对齐（本文件的重头）────────────────────────────────────────


def _item_context(selector: str = "li.item"):
    from omnicrawler.extraction.html_tools import parse_html

    document = parse_html(_ITEM_HTML)
    context = document.select_one(selector)
    assert context is not None
    return context


def _engine_value(rule, *, context_selector: str = "li.item") -> object:
    """把契约形状喂给**真实引擎**（`extractors._apply_rule` 是任务抽取的取值实现）。"""
    from omnicrawler.extraction.extractors import _apply_rule

    value, _trace = _apply_rule(_item_context(context_selector), rule)
    return value


def test_engine_honours_child_position() -> None:
    assert _engine_value({SELECTOR_KEY: "span.p"}) == "11"
    assert _engine_value({SELECTOR_KEY: "a.link", ATTR_KEY: "href"}) == "/a/1"


def test_engine_honours_element_position() -> None:
    """选择器留空 ⇒ 取**条目元素自身**的文本（契约声明如此，引擎行为亦如此）。"""
    assert _engine_value({SELECTOR_KEY: ""}) == "第一条 11"


def test_engine_honours_element_attr_position() -> None:
    """选择器留空 + attr ⇒ 取条目元素自身的属性。

    ★ 前提是**条目本身就是带该属性的元素**（例如"链接列表"里 item 就是 ``<a>``）。
    拿 ``<li>`` 当条目去取 ``href`` 会得到 ``None`` —— 那是**引擎正确**、配置错位；
    这也是本契约把"位置"显式化的价值：错位一眼可见，而不是拿到一列空值。
    """
    assert _engine_value({SELECTOR_KEY: "", ATTR_KEY: "href"}, context_selector="a.beacon") == "/b/1"
    assert (
        _engine_value({SELECTOR_KEY: "", ATTR_KEY: "data-kind"}, context_selector="a.beacon") == "primary"
    )
    # 反向：item 上没有该属性 ⇒ 取到空（配置错位），与契约里"位置必须与条目形态匹配"一致
    assert _engine_value({SELECTOR_KEY: "", ATTR_KEY: "href"}, context_selector="li.item") is None


def test_engine_accepts_the_gui_key_name_after_normalization() -> None:
    """GUI/分析侧用 `attribute` 写的规则，**归一后引擎必须认**（两键名收敛的意义）。"""
    gui_rule = {SELECTOR_KEY: "", ATTRIBUTE_ALIAS: "href"}
    assert _engine_value(normalize_rule(gui_rule)) == _engine_value({SELECTOR_KEY: "", ATTR_KEY: "href"})
