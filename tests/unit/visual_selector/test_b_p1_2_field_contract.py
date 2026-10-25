"""B-P1-2：可视化选择器生成配置必须与提取引擎契约一致。

引擎契约（extraction/extractors.py）：
- XPath 分支读 ``xpath`` + ``attr``；CSS 分支读 ``selector`` + ``attr``；
- 浏览器点选产出 XPath，因此生成键名为 ``xpath``，属性键名为 ``attr``；
- 纯文本字段不带 ``attr``（引擎回退 text_content）。
另校验：``set_seed_url`` 回传种子；未设置时回退 example.com 占位。
"""

from __future__ import annotations

from omnicrawler.visual_selector.field_converter import FieldConverter, SelectionToFieldSpec, SelectedElement


def _spec(tag: str, xpath: str) -> SelectionToFieldSpec:
    element = SelectedElement(
        xpath=xpath, all_xpaths=[xpath], tag=tag, text="示例",
    )
    return SelectionToFieldSpec(elements=[element], common_xpath=xpath, field_prefix="f")


def test_image_rule_uses_xpath_and_attr_src() -> None:
    fields = _spec("IMG", "//img").to_omnicrawler_fields()
    rule = fields["f_图片地址"]
    assert rule["xpath"] == "//img"
    assert rule["attr"] == "src"
    assert "selector" not in rule
    assert "attribute" not in rule


def test_link_text_has_no_attr_href_field_uses_attr_href() -> None:
    fields = _spec("A", "//a").to_omnicrawler_fields()
    text_rule = fields["f_链接文本"]
    href_rule = fields["f_链接地址"]
    assert text_rule["xpath"] == "//a"
    assert "attr" not in text_rule
    assert href_rule["xpath"] == "//a"
    assert href_rule["attr"] == "href"


def test_input_rule_uses_attr_value() -> None:
    fields = _spec("INPUT", "//input").to_omnicrawler_fields()
    rule = fields["f_值"]
    assert rule["xpath"] == "//input"
    assert rule["attr"] == "value"


def test_plain_text_rule_uses_xpath_only() -> None:
    fields = _spec("DIV", "//div").to_omnicrawler_fields()
    rule = fields["f_文本"]
    assert rule["xpath"] == "//div"
    assert "attr" not in rule


def test_seed_url_is_propagated_to_yaml() -> None:
    converter = FieldConverter()
    converter.set_seed_url("https://example.org/page")
    config = converter.to_yaml()
    assert config["source"]["seeds"] == ["https://example.org/page"]


def test_missing_seed_url_falls_back_to_placeholder() -> None:
    config = FieldConverter().to_yaml()
    assert config["source"]["seeds"] == ["https://example.com"]
